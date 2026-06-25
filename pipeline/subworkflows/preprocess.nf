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
// Concatenates multiple R1 or R2 files into a single .fastq or .fastq.gz
// Preserves compression state: if all inputs are .gz, output is .gz
// Works with both GCS and local paths (Nextflow stages GCS files automatically)

process CONCAT {
    tag "${read_type}"

    input:
    tuple val(read_type), path(files)
    // read_type: "R1" or "R2"
    // files: one or more .fastq/.fastq.gz (Nextflow stages from GCS if needed)

    output:
    tuple val(read_type), path("${read_type}.fastq*"), emit: fastq
    // Output matches input compression: .fastq.gz if inputs were gz, .fastq otherwise

    script:
    """
    # Determine if input files are gzipped
    first=\$(echo "${files}" | tr ' ' '\\n' | head -1)
    if [[ "\$first" == *.gz ]]; then
        # All inputs are gzipped — concatenate directly
        echo "Concatenating gzipped files: ${files}"
        cat ${files} > ${read_type}.fastq.gz
    else
        # Inputs are uncompressed
        echo "Concatenating uncompressed files: ${files}"
        cat ${files} > ${read_type}.fastq
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
    """
    if [[ "${gz_file}" == *.gz ]]; then
        echo "Decompressing: ${gz_file}"
        gunzip -c "${gz_file}" > merged.fastq
    else
        echo "File already uncompressed: ${gz_file}"
        mv "${gz_file}" merged.fastq
    fi
    
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

    # Count input lines (divide by 4 for FASTQ records)
    r1_lines=\$(zcat -f input_R1.${r1_ext} | wc -l)
    r2_lines=\$(zcat -f input_R2.${r2_ext} | wc -l)
    echo "Input R1 lines: \$r1_lines (reads: \$(( r1_lines / 4 )))"
    echo "Input R2 lines: \$r2_lines (reads: \$(( r2_lines / 4 )))"




    fastp \
        --in1 input_R1.${r1_ext} \
        --in2 input_R2.${r2_ext} \
        -m \
        --merged_out merged.fastq \
        --correction \
        -w ${params.fastp_threads} \
        -h fastp.html \
        -j fastp.json \


    merged_lines=\$(wc -l < merged.fastq)
    echo "Output merged.fastq lines: \$merged_lines (reads: \$(( merged_lines / 4 )))"
    echo "Output merged.fastq size: \$(wc -c < merged.fastq) bytes"
    """
    

    stub:
    """
    touch merged.fastq fastp.html fastp.json
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
    fastq = fastq_out
}