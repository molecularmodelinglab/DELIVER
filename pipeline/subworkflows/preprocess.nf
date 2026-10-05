#!/usr/bin/env nextflow

/**
 * ==============================================================================
 * PREPROCESS SUBWORKFLOW
 * ==============================================================================
 * Each LANE is processed separately and in parallel, all the way into DELI:
 *   .ora lanes -> ORA_DECOMPRESS (per read file) -> .fastq.gz
 *   paired     -> FASTP_MERGE per lane           -> <lane>.merged.fastq.gz
 *   single-end -> the lane's R1 goes straight on (SPLIT reads .gz/.fastq)
 *   FASTQC runs per lane on the raw (decompressed) reads.
 *
 * Lanes used to be concatenated first (CONCAT) so fastp saw one R1/R2 pair.
 * That serialized the longest steps (fastp, split) on one task each and made
 * fastp's memory grow with the whole run. Per-lane results are identical:
 * fastp merges each read pair independently, and DELi decoding is per read
 * (chunk layout never changes counts).
 *
 * GCS inputs (gs://…) are staged automatically by Nextflow.
 * ==============================================================================
 */

nextflow.enable.dsl = 2

// ============================================================================
// ORA_DECOMPRESS — Illumina .ora -> .fastq.gz, one read file per task
// ============================================================================
// fastp and FastQC cannot read ORA. orad writes gzip natively (no --raw), so
// the output stays compressed. Only .ora inputs come through here; .fastq.gz
// and .fastq files go to fastp/FastQC directly.

process ORA_DECOMPRESS {
    tag "${lane} ${read}"

    input:
    tuple val(lane), val(read), path(ora_file)

    output:
    tuple val(lane), val(read), path("${lane}_${read}.fastq.gz"), emit: fastq

    script:
    // ORA_REF_PATH is only consulted for reference-based ORA; unset is fine for
    // standard reference-free FASTQ .ora. See params.ora_reference.
    def ora_ref = params.ora_reference ? "export ORA_REF_PATH='${params.ora_reference}'" : "true"
    """
    ${ora_ref}
    orad -c -t ${task.cpus} "${ora_file}" > ${lane}_${read}.fastq.gz
    """

    stub:
    """
    cp "${ora_file}" ${lane}_${read}.fastq.gz
    """
}


process FASTP_MERGE {
    tag "${lane}"
    publishDir "${params.out_dir}/qc", mode: 'copy', saveAs: { fn -> fn.endsWith('.merged.fastq.gz') ? null : fn }

    input:
    tuple val(lane), path(r1), path(r2)

    output:
    path "${lane}.merged.fastq.gz", emit: fastq
    path "${lane}_fastp.html",      emit: html
    path "${lane}_fastp.json",      emit: json

    script:
    def r1_ext = r1.name.endsWith('.gz') ? 'gz' : 'fastq'
    def r2_ext = r2.name.endsWith('.gz') ? 'gz' : 'fastq'
    """
    ln -s ${r1} input_R1.${r1_ext}
    ln -s ${r2} input_R2.${r2_ext}

    # NOTE: read counts are NOT computed with `zcat | wc -l` here — for a
    # ~400M-read run that decompresses both inputs an extra time end-to-end
    # (a large, avoidable I/O pass just for a log line). fastp already reports
    # before/after read counts in fastp.json.
    # merged_out ends in .gz so fastp gzip-compresses the merged output itself
    # (multi-threaded, using the same -w workers) instead of writing raw FASTQ.
    fastp \
        --in1 input_R1.${r1_ext} \
        --in2 input_R2.${r2_ext} \
        -m \
        --merged_out ${lane}.merged.fastq.gz \
        --correction \
        -w ${params.fastp_threads} \
        -h ${lane}_fastp.html \
        -j ${lane}_fastp.json

    echo "fastp merge complete — read counts available in fastp.json"
    """


    stub:
    """
    printf '@stub_read1\\nACGTACGT\\n+\\nIIIIIIII\\n' | gzip -c > ${lane}.merged.fastq.gz
    touch ${lane}_fastp.html ${lane}_fastp.json
    """
}

process ORA_DIAGNOSTICS {
    tag "ora_diagnostics"
    publishDir "${params.out_dir}/qc", mode: 'copy'

    input:
    path r1_files
    path r2_files
    // r2_files may be an empty list (single-end); its loop then runs zero times.

    output:
    path "ora_diagnostics.txt", emit: report

    script:
    // Same ORA reference handling as CONCAT/DECOMPRESS (unset is fine for
    // standard reference-free FASTQ .ora).
    def ora_ref = params.ora_reference ? "export ORA_REF_PATH='${params.ora_reference}'" : "true"
    """
    ${ora_ref}
    set -o pipefail   # so a failed orad in a `decode | wc -l` pipe fails the task
    report=ora_diagnostics.txt
    : > "\$report"
    say() { echo "\$@" | tee -a "\$report"; }

    # decode(): stream ONE file to raw FASTQ on stdout, dispatching by type
    #   .ora -> orad (DRAGEN)   .gz -> zcat   else -> cat
    decode() {
        case "\$1" in
            *.ora) orad -c --raw -t ${task.cpus} "\$1" ;;
            *.gz)  zcat "\$1" ;;
            *)     cat "\$1" ;;
        esac
    }

    say "==================================================================="
    say " ORA DIAGNOSTICS   (extra/temporary step — not part of final code) "
    say "==================================================================="

    run_start=\$SECONDS

    # ---- [1] rows in each single file (decode once, STREAMED, timed) --------
    # Pure streaming: decode -> wc -l, nothing is written to disk, so no large
    # scratch disk is needed. Combining files is plain concatenation, so the
    # combined and total row counts (steps 2 & 3) are just the SUM of the
    # per-file line counts collected here.
    say ""
    say "[1] Rows in each single file"
    r1_lines=0
    for f in ${r1_files}; do
        t0=\$SECONDS
        lines=\$(decode "\$f" | wc -l)
        dt=\$(( SECONDS - t0 ))
        say "    [R1] \$f : \$lines lines / \$(( lines / 4 )) reads  (\${dt}s)"
        r1_lines=\$(( r1_lines + lines ))
    done
    r2_lines=0
    for f in ${r2_files}; do
        t0=\$SECONDS
        lines=\$(decode "\$f" | wc -l)
        dt=\$(( SECONDS - t0 ))
        say "    [R2] \$f : \$lines lines / \$(( lines / 4 )) reads  (\${dt}s)"
        r2_lines=\$(( r2_lines + lines ))
    done

    # ---- [2] rows after combining the 2 reads (R1 & R2 concatenated) --------
    say ""
    say "[2] Rows after combining the 2 reads"
    say "    R1 combined : \$r1_lines lines / \$(( r1_lines / 4 )) reads"
    say "    R2 combined : \$r2_lines lines / \$(( r2_lines / 4 )) reads"

    # ---- [3] total rows after decompress (R1 + R2) --------------------------
    say ""
    say "[3] Total rows after decompress (R1 + R2)"
    total_lines=\$(( r1_lines + r2_lines ))
    say "    total : \$total_lines lines / \$(( total_lines / 4 )) reads"

    # ---- timing summary -----------------------------------------------------
    say ""
    say "[time] total wall time: \$(( SECONDS - run_start ))s"
    """

    stub:
    """
    echo "stub ora diagnostics" > ora_diagnostics.txt
    """
}


// ============================================================================
// FASTQC PROCESS
// ============================================================================
// Runs FastQC on one lane's raw (pre-merge) reads: per-base quality, GC
// content, adapter content, duplication levels, overrepresented sequences.
// R1 only on the single-end path, R1 + R2 (one task) on the paired-end path.

process FASTQC {
    tag "${lane}"
    publishDir "${params.out_dir}/qc", mode: 'copy'

    input:
    tuple val(lane), path(reads)

    output:
    path "*_fastqc.html", emit: html
    path "*_fastqc.zip",  emit: zip

    script:
    // Prefix inputs with selection_id via symlink so FastQC's output names
    // carry the selection, e.g. L007_R1.fastq.gz ->
    // ${params.selection_id}_L007_R1_fastqc.html — unambiguous when reports
    // from many runs are aggregated (MultiQC).
    """
    renamed=""
    for f in ${reads}; do
        link="${params.selection_id}_\$f"
        ln -s "\$f" "\$link"
        renamed="\$renamed \$link"
    done
    fastqc --threads ${params.fastqc_threads} \$renamed
    """

    stub:
    """
    for f in ${reads}; do
        base=\$(basename "\$f" | sed -E 's/\\.(fastq|fq)(\\.gz)?\$//')
        touch "${params.selection_id}_\${base}_fastqc.html" "${params.selection_id}_\${base}_fastqc.zip"
    done
    """
}


// ============================================================================
// FASTP_QC PROCESS
// ============================================================================
// Runs fastp in QC-only mode (no merging, output reads discarded).
// Used on the single-end path where FASTP_MERGE does not run.

process FASTP_QC {
    publishDir "${params.out_dir}/qc", mode: 'copy'

    input:
    path r1

    output:
    path "fastp_qc.html", emit: html
    path "fastp_qc.json", emit: json

    script:
    def r1_ext = r1.name.endsWith('.gz') ? 'gz' : 'fastq'
    """
    ln -s ${r1} input_R1.${r1_ext}
    fastp \
        --in1 input_R1.${r1_ext} \
        -o /dev/null \
        -h fastp_qc.html \
        -j fastp_qc.json \
        -w ${params.fastp_threads}
    """

    stub:
    """
    touch fastp_qc.html fastp_qc.json
    """
}


// ============================================================================
// PREPROCESS WORKFLOW
// ============================================================================

workflow PREPROCESS {
    main:

    // ========================================================================
    // Step 1: pair the lanes
    // ========================================================================
    // read_1[i] pairs with read_2[i] (the same order the old concatenation
    // relied on). When names follow Illumina's _R1_/_R2_ convention, a
    // mismatched order is caught here instead of silently mis-pairing reads.
    def as_list = { v -> v instanceof List ? v : v.toString().split(',').collect { it.trim() }.findAll { it } }
    def r1_list = as_list(params.read_1)
    def r2_list = params.read_2 ? as_list(params.read_2) : []
    def paired  = !r2_list.isEmpty()
    if (paired && r1_list.size() != r2_list.size()) {
        error("read_1 has ${r1_list.size()} file(s) but read_2 has ${r2_list.size()} — list one R2 per R1, in the same lane order")
    }
    def base_of = { String p -> p.tokenize('/').last() }
    if (paired) {
        r1_list.eachWithIndex { r1, i ->
            def b1 = base_of(r1), b2 = base_of(r2_list[i])
            if (b1 =~ /_R1[_.]/ && b2 =~ /_R2[_.]/ && b1.replaceFirst(/_R1([_.])/, '_R2$1') != b2) {
                error("read_1[${i}] and read_2[${i}] are not mates: ${b1} vs ${b2} — list R2 files in the same lane order as R1")
            }
        }
    }
    // Lane id: the Illumina lane (L007) when present and unique, else 1-based position.
    def lane_ids = r1_list.withIndex().collect { r1, i ->
        def m = base_of(r1) =~ /_(L\d{3})_/
        m.find() ? m.group(1) : "lane${i + 1}"
    }
    if (lane_ids.toSet().size() != lane_ids.size()) {
        lane_ids = lane_ids.withIndex().collect { id, i -> "${i + 1}_${id}".toString() }
    }

    // One entry per read file: [lane, 'R1'|'R2', file]
    def read_files = []
    r1_list.eachWithIndex { r1, i ->
        read_files << [lane_ids[i], 'R1', file(r1)]
        if (paired) read_files << [lane_ids[i], 'R2', file(r2_list[i])]
    }

    // ========================================================================
    // Step 2: ORA -> .fastq.gz where needed (per read file, in parallel)
    // ========================================================================
    by_type = Channel.fromList(read_files).branch {
        ora:   it[2].name.endsWith('.ora')
        ready: true
    }
    reads_ch = ORA_DECOMPRESS(by_type.ora).fastq.mix(by_type.ready)

    r1_ch = reads_ch.filter { it[1] == 'R1' }.map { lane, read, f -> [lane, f] }

    // ========================================================================
    // Step 3: per-lane QC and merge
    // ========================================================================
    if (paired) {
        r2_ch      = reads_ch.filter { it[1] == 'R2' }.map { lane, read, f -> [lane, f] }
        lane_pairs = r1_ch.join(r2_ch, failOnMismatch: true, failOnDuplicate: true)   // [lane, r1, r2]

        FASTQC(lane_pairs.map { lane, r1, r2 -> [lane, [r1, r2]] })
        fastq_out = FASTP_MERGE(lane_pairs).fastq
    } else {
        FASTQC(r1_ch.map { lane, r1 -> [lane, [r1]] })
        fastq_out = r1_ch.map { lane, r1 -> r1 }
    }

    def source = params.read_1.toString().contains("gs://") ? "GCS bucket" : "local/HPC"
    log.info "========================================"
    log.info "PREPROCESS: ${paired ? 'Paired-end' : 'Single-end'}, ${r1_list.size()} lane(s), processed in parallel"
    log.info "========================================"
    log.info "Source : ${source}"
    r1_list.eachWithIndex { f, i ->
        log.info "  ${lane_ids[i]}  R1 ${f}"
        if (paired) log.info "  ${lane_ids[i]}  R2 ${r2_list[i]}"
    }
    log.info "========================================"

    emit:
    fastq = fastq_out          // one merged (or single-end) FASTQ per lane
}
