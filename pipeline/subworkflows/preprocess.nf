#!/usr/bin/env nextflow

/**
 * ==============================================================================
 * PREPROCESS SUBWORKFLOW
 * ==============================================================================
 * Handles:
 * - Input file resolution (GCS or local)
 * - Concatenation of multiple R1/R2 files
 * - Paired-end merging (fastp) or single-end decompression
 * - Output: uncompressed .fastq ready for DELI
 *
 * GCS-specific notes:
 * - Input paths from GCS (gs://bucket/file.fastq.gz) are auto-staged
 * - fastp reads .gz files directly (no upfront decompression needed)
 * - All output files written to container work dir, then published to out_dir
 * ==============================================================================
 */

nextflow.enable.dsl = 2

// ============================================================================
// CONCAT PROCESS
// ============================================================================
// Concatenates multiple R1 or R2 lane files into a single FASTQ, dispatching on
// file type so each input is decompressed with the right tool:
//   .gz  -> gzip stream        (zcat, or a direct cat when every input is .gz)
//   .ora -> Illumina ORA       (orad -c --raw, DRAGEN ORA decompression)
//   else -> already uncompressed (cat)
// Output compression:
//   all inputs .gz            -> ${read_type}.fastq.gz  (cheap: gzip members concat)
//   anything else (incl .ora) -> ${read_type}.fastq     (uncompressed)
// Both forms are handled downstream (fastp uses `zcat -f`; DECOMPRESS gunzips).
// Works with GCS and local paths (Nextflow stages remote files automatically).

process CONCAT {
    tag "${read_type}"

    input:
    tuple val(read_type), path(files)
    // read_type: "R1" or "R2"
    // files: one or more .fastq / .fastq.gz / .fastq.ora (staged from GCS if needed)

    output:
    tuple val(read_type), path("${read_type}.fastq*"), emit: fastq

    script:
    // ORA_REF_PATH is only consulted for reference-based ORA; unset is fine for
    // standard reference-free FASTQ .ora. See params.ora_reference.
    def ora_ref = params.ora_reference ? "export ORA_REF_PATH='${params.ora_reference}'" : "true"
    """
    ${ora_ref}

    # Classify inputs so we can take the cheapest correct path.
    any_ora=false
    all_gz=true
    for f in ${files}; do
        case "\$f" in
            *.ora) any_ora=true; all_gz=false ;;
            *.gz)  ;;
            *)     all_gz=false ;;
        esac
    done

    if [[ "\$all_gz" == true ]]; then
        # Fast path: concatenated gzip members form one valid gzip stream.
        echo "Concatenating gzipped files -> ${read_type}.fastq.gz"
        cat ${files} > ${read_type}.fastq.gz
    else
        # Mixed / uncompressed / ORA: decompress per file type into one FASTQ.
        echo "Decompressing + concatenating (any_ora=\$any_ora) -> ${read_type}.fastq"
        : > ${read_type}.fastq
        for f in ${files}; do
            case "\$f" in
                *.ora)
                    # Illumina ORA (DRAGEN). -c: to stdout, --raw: uncompressed FASTQ.
                    # If your orad build uses different flags, change them here only.
                    orad -c --raw -t ${task.cpus} "\$f" >> ${read_type}.fastq
                    ;;
                *.gz)
                    zcat "\$f" >> ${read_type}.fastq
                    ;;
                *)
                    cat "\$f" >> ${read_type}.fastq
                    ;;
            esac
        done
    fi
    """

    stub:
    """
    cat ${files} > ${read_type}.fastq
    """
}

// ============================================================================
// DECOMPRESS PROCESS
// ============================================================================
// Decompresses .fastq.gz to .fastq for single-end path.
// If input is already uncompressed, just renames it.
// Not published — DELI reads directly from container work dir.

process DECOMPRESS {
    tag "decompress"

    input:
    path gz_file
    // File object staging from GCS if needed (Nextflow automatic)

    output:
    path "merged.fastq", emit: fastq

    script:
    def ora_ref = params.ora_reference ? "export ORA_REF_PATH='${params.ora_reference}'" : "true"
    """
    ${ora_ref}
    case "${gz_file}" in
        *.ora)
            echo "Decompressing ORA: ${gz_file}"
            orad -c --raw -t ${task.cpus} "${gz_file}" > merged.fastq
            ;;
        *.gz)
            echo "Decompressing: ${gz_file}"
            gunzip -c "${gz_file}" > merged.fastq
            ;;
        *)
            echo "File already uncompressed: ${gz_file}"
            mv "${gz_file}" merged.fastq
            ;;
    esac

    # Verify output
    if [[ -f merged.fastq ]]; then
        echo "Output file size: \$(wc -c < merged.fastq) bytes"
    fi
    """

    stub:
    """
    cp ${gz_file} merged.fastq
    """
}


process FASTP_MERGE {
    publishDir "${params.out_dir}/qc", mode: 'copy', saveAs: { fn -> fn == 'merged.fastq' ? null : fn }

    input:
    path r1
    path r2

    output:
    path "merged.fastq", emit: fastq
    path "fastp.html",   emit: html
    path "fastp.json",   emit: json

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
    fastp \
        --in1 input_R1.${r1_ext} \
        --in2 input_R2.${r2_ext} \
        -m \
        --merged_out merged.fastq \
        --correction \
        -w ${params.fastp_threads} \
        -h fastp.html \
        -j fastp.json

    echo "fastp merge complete — read counts available in fastp.json"
    """
    

    stub:
    """
    touch merged.fastq fastp.html fastp.json
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
    // Step 1: Resolve input R1 files
    // ========================================================================
    // Use Channel.fromPath — preserves gs:// URIs correctly.
    // file() at workflow scope strips the gs:// scheme to a local path.

    r1_ch = (params.read_1 instanceof List)
        ? Channel.fromPath(params.read_1)
        : Channel.fromPath(params.read_1.toString().split(',').collect { it.trim() })

    // Collect back into a list for CONCAT (which expects a list, not a channel of files)
    r1_files = r1_ch.collect()

    // ========================================================================
    // Step 2: Determine execution path (paired-end vs single-end)
    // ========================================================================

    if (params.read_2) {

        r2_ch = (params.read_2 instanceof List)
            ? Channel.fromPath(params.read_2)
            : Channel.fromPath(params.read_2.toString().split(',').collect { it.trim() })

        r2_files = r2_ch.collect()

        // === EXTRA / TEMPORARY: .ora diagnostics (counts + timing) ===========
        // diag = ORA_DIAGNOSTICS(r1_files, r2_files)

        // --- production merge flow (DISABLED during diagnostics; restore for final) ---
        r1_ch_tuple = r1_files.map { files -> tuple("R1", files) }
        r2_ch_tuple = r2_files.map { files -> tuple("R2", files) }
        reads_ch = r1_ch_tuple.mix(r2_ch_tuple)
        
        concat_out = CONCAT(reads_ch)
        
        r1_fastq = concat_out.fastq.filter { it[0] == "R1" }.map { it[1] }
        r2_fastq = concat_out.fastq.filter { it[0] == "R2" }.map { it[1] }
        
        fastq_out = FASTP_MERGE(r1_fastq, r2_fastq).fastq

        // Logging — done after channel ops so paths are still URI strings
        def r1_source = params.read_1.toString().contains("gs://") ? "GCS bucket" : "local/HPC"
        def r2_source = params.read_2.toString().contains("gs://") ? "GCS bucket" : "local/HPC"
        def r1_list = (params.read_1 instanceof List) ? params.read_1 : params.read_1.toString().split(',').collect { it.trim() }
        def r2_list = (params.read_2 instanceof List) ? params.read_2 : params.read_2.toString().split(',').collect { it.trim() }
        log.info "========================================"
        log.info "PREPROCESS: Paired-End Mode"
        log.info "========================================"
        log.info "R1 source : ${r1_source} (${r1_list.size()} file(s))"
        log.info "R2 source : ${r2_source} (${r2_list.size()} file(s))"
        r1_list.eachWithIndex { f, i -> log.info "  R1[${i+1}] ${f}" }
        r2_list.eachWithIndex { f, i -> log.info "  R2[${i+1}] ${f}" }
        log.info "Merging with fastp..."
        log.info "========================================"

    } else {

        // === EXTRA / TEMPORARY: .ora diagnostics (counts + timing) ===========
        // Single-end: pass an empty list for R2 (its loop runs zero times).
        // diag = ORA_DIAGNOSTICS(r1_files, Channel.value([]))

        // --- production single-end flow (DISABLED during diagnostics; restore for final) ---
        reads_ch = r1_files.map { files -> tuple("R1", files) }
        concat_out = CONCAT(reads_ch)
        FASTP_QC(concat_out.fastq.map { it[1] })
        fastq_out = DECOMPRESS(concat_out.fastq.map { it[1] }).fastq

        def r1_source = params.read_1.toString().contains("gs://") ? "GCS bucket" : "local/HPC"
        def r1_list = (params.read_1 instanceof List) ? params.read_1 : params.read_1.toString().split(',').collect { it.trim() }
        log.info "========================================"
        log.info "PREPROCESS: Single-End Mode"
        log.info "========================================"
        log.info "R1 source : ${r1_source} (${r1_list.size()} file(s))"
        r1_list.eachWithIndex { f, i -> log.info "  R1[${i+1}] ${f}" }
        log.info "Decompressing..."
        log.info "========================================"
    }

    emit:
    fastq = fastq_out          // production output
    // report = diag.report          // ORA file -diagnostic-only output (temporary)
}