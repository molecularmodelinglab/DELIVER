nextflow.enable.dsl = 2

// Preprocessing subworkflow.
// Steps:
//   1. Concatenate lanes (multiple R1 / R2 files) — files stay compressed if they were .gz
//   2. If paired-end (read_2 provided): fastp merges R1+R2 (reads .gz natively) → merged.fastq
//   3. If single-end: decompress the concatenated file if needed → R1.fastq
//
// Only the final merged/decompressed FASTQ is published. Intermediate files are not saved.
// fastp QC reports (HTML + JSON) are published to out_dir/qc.

// ---------------------------------------------------------------------------
// CONCAT
//   Concatenates one or more FASTQ files. Files stay compressed if they were .gz —
//   gzip format supports concatenation natively (cat a.gz b.gz > combined.gz is valid).
//   Not published — intermediate file used only within the workflow.
// ---------------------------------------------------------------------------
process CONCAT {
    input:
    tuple val(read_type), path(files)  // ("R1"|"R2", one or more files — .fastq or .fastq.gz)

    output:
    tuple val(read_type), path("${read_type}.fastq*"), emit: fastq
    // output extension matches input: .fastq.gz if inputs were gz, .fastq otherwise

    script:
    """
    first=\$(echo "${files}" | tr ' ' '\\n' | head -1)
    if [[ "\$first" == *.gz ]]; then
        cat ${files} > ${read_type}.fastq.gz
    else
        cat ${files} > ${read_type}.fastq
    fi
    """

    stub:
    """
    cat ${files} > ${read_type}.fastq
    """
}

// ---------------------------------------------------------------------------
// DECOMPRESS
//   Decompresses a .fastq.gz to .fastq for single-end path.
//   If input is already uncompressed, just renames it.
//   Not published — DELI uses the file directly from the work dir.
// ---------------------------------------------------------------------------
process DECOMPRESS {
    input:
    path gz_file

    output:
    path "merged.fastq", emit: fastq

    script:
    """
    if [[ "${gz_file}" == *.gz ]]; then
        gunzip -c "${gz_file}" > merged.fastq
    else
        mv "${gz_file}" merged.fastq
    fi
    """

    stub:
    """
    cp ${gz_file} merged.fastq
    """
}

// ---------------------------------------------------------------------------
// FASTP_MERGE
//   Merges paired-end R1 + R2 into a single-end .fastq using fastp.
//   Reads .gz natively — no upfront decompression needed.
//   Publishes QC reports to out_dir/qc.
// ---------------------------------------------------------------------------
process FASTP_MERGE {
    publishDir "${params.out_dir}/qc", mode: 'copy'

    input:
    path r1   // .fastq or .fastq.gz
    path r2   // .fastq or .fastq.gz

    output:
    path "merged.fastq", emit: fastq
    path "fastp.html",   emit: html
    path "fastp.json",   emit: json

    script:
    """
    fastp \
        --in1 ${r1} \
        --in2 ${r2} \
        -m \
        --merged_out merged.fastq \
        --correction \
        -w ${params.fastp_threads} \
        -h fastp.html \
        -j fastp.json
    """

    stub:
    """
    touch merged.fastq fastp.html fastp.json
    """
}

// ---------------------------------------------------------------------------
// WORKFLOW
// ---------------------------------------------------------------------------
workflow PREPROCESS {
    main:
    r1_files = params.read_1 instanceof List
        ? params.read_1.collect { file(it) }
        : [file(params.read_1)]

    if (params.read_2) {
        r2_files = params.read_2 instanceof List
            ? params.read_2.collect { file(it) }
            : [file(params.read_2)]

        // R1 and R2 concatenated via one channel — avoids DSL2 duplicate-process error
        reads_ch = Channel.of(["R1", r1_files], ["R2", r2_files])
        concat_out = CONCAT(reads_ch)

        r1_fastq = concat_out.fastq.filter { it[0] == "R1" }.map { it[1] }
        r2_fastq = concat_out.fastq.filter { it[0] == "R2" }.map { it[1] }

        // fastp reads .gz natively and outputs uncompressed merged.fastq directly
        fastq_out = FASTP_MERGE(r1_fastq, r2_fastq).fastq
    } else {
        reads_ch = Channel.of(["R1", r1_files])
        concat_out = CONCAT(reads_ch)
        // Decompress at the end if needed — only for single-end path
        fastq_out = DECOMPRESS(concat_out.fastq.map { it[1] }).fastq
    }

    emit:
    fastq = fastq_out  // single uncompressed FASTQ ready for DELI
}
