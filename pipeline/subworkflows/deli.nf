nextflow.enable.dsl = 2

// ============================================================================
// DELIVER — generates decode.yaml from params
// ============================================================================

process GenerateDecodeYaml {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path fastq_file  // Path input (not val) for proper GCS staging

    output:
    path "${params.selection_id}_${params.target_id}_${params.date_ran}.yaml", emit: yaml

    script:
    def yaml_name = "${params.selection_id}_${params.target_id}_${params.date_ran}.yaml"
    def files_py  = "[\"${fastq_file}\"]"  // Use the staged path directly
    def libs_py   = params.libraries instanceof List
        ? "[" + params.libraries.collect { "\"${it}\"" }.join(", ") + "]"
        : "[\"${params.libraries}\"]"
    """
    #!/usr/bin/env python
    import yaml

    config = {
        'selection_id':        '${params.selection_id}',
        'target_id':           '${params.target_id}',
        'selection_condition': '${params.selection_condition}',
        'date_ran':            '${params.date_ran}',
        'additional_info':     '${params.additional_info}',
        'sequence_files': ${files_py},
        'libraries':      ${libs_py},
        'decode_settings': {
            'library_error_tolerance': ${params.library_error_tolerance},
            'min_library_overlap':     ${params.min_library_overlap},
            'revcomp':                 '${params.revcomp}',
            'demultiplexer_algorithm': '${params.demultiplexer_algorithm}',
            'demultiplexer_mode':      '${params.demultiplexer_mode}',
            'realign':                 '${params.realign}',
            'wiggle':                  '${params.wiggle}',
        },
    }

    with open('${yaml_name}', 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    """
}

// ============================================================================
// PROCESSES
// ============================================================================

process ExtractSequenceFiles {
    input:
    path selection_file

    output:
    path "selection_id.txt", emit: selection_id
    path "files.txt", emit: files

    script:
    """
    #!/usr/bin/env python
    import yaml

    with open("${selection_file}") as f:
        config = yaml.safe_load(f)

    selection_id = config.get('selection_id', 'unknown')
    with open('selection_id.txt', 'w') as f:
        f.write(selection_id + '\\n')

    sequence_files = config.get('sequence_files', [])
    with open('files.txt', 'w') as f:
        if isinstance(sequence_files, list):
            f.write('\\n'.join(sequence_files) + '\\n')
        else:
            f.write(sequence_files + '\\n')
    """
}

process DecodeChunk {
    tag "${fastq_chunk.name}"

    input:
    path fastq_chunk   // Path input ensures GCS staging
    path selection_file
    val prefix
    val deli_args

    output:
    path "${prefix}_${fastq_chunk.baseName}_decoded.tsv", emit: decoded_tsv
    path "${prefix}_${fastq_chunk.baseName}_decode_statistics.json", emit: decode_stats
    path "deli.log", emit: deli_log

    script:
    """
    echo "=== WORKING DIRECTORY CONTENTS ==="
    ls -lah

    echo ""
    echo "=== INPUT FILE DETAILS ==="
    echo "fastq_chunk path: ${fastq_chunk}"
    echo "fastq_chunk name: ${fastq_chunk.name}"
    echo "fastq_chunk baseName: ${fastq_chunk.baseName}"
    echo "fastq_chunk size:"
    ls -lh ${fastq_chunk} || echo "FILE NOT FOUND"
    echo "fastq_chunk file type:"
    file ${fastq_chunk} || echo "file command not available"

    echo ""
    echo "=== FASTQ PREVIEW (first 8 lines) ==="
    head -n 8 ${fastq_chunk} || echo "CANNOT READ FASTQ"

    echo ""
    echo "=== SELECTION FILE DETAILS ==="
    echo "selection_file path: ${selection_file}"
    echo "selection_file name: ${selection_file.name}"
    echo "selection_file content:"
    cat ${selection_file} || echo "CANNOT READ YAML"

    echo ""
    echo "=== PREFIX & DELI_ARGS ==="
    echo "prefix: ${prefix}"
    echo "deli_args: ${deli_args}"

    echo ""
    echo "=== RUNNING DELI ==="


    
    mkdir -p decoded_output

    deli ${deli_args} decode run \
        "${selection_file}" \
        "${fastq_chunk}" \
        --out-dir ./ \
        --prefix "${prefix}_${fastq_chunk.baseName}" \
        --skip-report
    """

    stub:
    """
    touch ${prefix}_${fastq_chunk.baseName}_decoded.tsv
    echo '{}' > ${prefix}_${fastq_chunk.baseName}_decode_statistics.json
    touch deli.log
    """
}

process MergeDecodeStatistics {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path("*_decode_statistics.json", arity: '1..*')
    path selection_file
    val prefix
    val deli_args

    output:
    path "${prefix}_decode_stats.json", emit: merged_stats

    script:
    """
    deli ${deli_args} decode merge-stats \
        *_decode_statistics.json \
        --selection-file "${selection_file}" \
        --out-loc "${prefix}_decode_stats.json"
    """

    stub:
    """
    echo '{}' > ${prefix}_decode_stats.json
    """
}

process CollectDecodeChunks {
    input:
    path("*_decoded.tsv", arity: '1..*')
    val prefix
    val deli_args

    output:
    path "${prefix}_collected.ndjson", emit: ndjson

    script:
    """
    deli ${deli_args} decode collect \
        *_decoded.tsv \
        --out-loc "${prefix}_collected.ndjson"
    """

    stub:
    """
    echo '{"library_id":"L01","bb_ids":"1,2,3","umi_counts":[{"k":"ACGTACGTACGT","c":1}]}' > ${prefix}_collected.ndjson
    """
}

process CountChunk {
    tag "${ndjson_chunk.name}"

    input:
    path ndjson_chunk
    val prefix
    val deli_args

    output:
    path "${ndjson_chunk.name}_counted.parquet", emit: counted

    script:
    def chunk_name = ndjson_chunk.name
    """
    deli ${deli_args} decode count \
        "${ndjson_chunk}" \
        --out-loc "${chunk_name}_counted.parquet" \
        --output-format parquet \
        --cluster-umis \
        --keep-raw-count \
        --keep-dedup-count
    """

    stub:
    """
    touch ${ndjson_chunk.name}_counted.parquet
    """
}

process CollectCountChunks {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path counted_files
    val prefix

    output:
    path "${prefix}_counts.parquet", emit: merged_counts

    script:
    """
    #!/usr/bin/env python
    import polars as pl
    files = sorted([f for f in "${counted_files}".split() if f.strip()])
    pl.scan_parquet(files).sink_parquet("${prefix}_counts.parquet")
    """

    stub:
    """
    touch ${prefix}_counts.parquet
    """
}

process SummarizeDecodeRun {
    publishDir "${params.out_dir}", mode: 'move'

    input:
    path merged_counts
    path decode_stats
    val prefix
    val deli_args

    output:
    path "${prefix}_decode_summary.json", emit: final_stats

    script:
    """
    deli ${deli_args} decode summarize \
        "${merged_counts}" \
        "${decode_stats}" \
        --out-loc "${prefix}_decode_summary.json"
    """

    stub:
    """
    echo '{}' > ${prefix}_decode_summary.json
    """
}

process WriteDecodeReport {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path final_stats
    path selection_file
    val prefix
    val deli_args

    output:
    path "${prefix}_decode_report.html", emit: report

    script:
    """
    deli ${deli_args} decode report \
        "${final_stats}" \
        --selection-file "${selection_file}" \
        --out-loc "${prefix}_decode_report.html"
    """

    stub:
    """
    touch ${prefix}_decode_report.html
    """
}

// ============================================================================
// WORKFLOW
// ============================================================================

workflow DELI {
    take:
    fastq_files  // Channel of Path objects
    fastq_uri

    main:
    // Generate decode.yaml — pass the actual Path, not a string
    selection_file_path = GenerateDecodeYaml(fastq_uri).yaml.first()

    def safePathPattern = ~/^[\w.\-\/]+$/
    if (params.deli_data_dir && !(params.deli_data_dir ==~ safePathPattern)) {
        error("Invalid characters in --deli_data_dir parameter")
    }
    if (params.config_file && !(params.config_file ==~ safePathPattern)) {
        error("Invalid characters in --config_file parameter")
    }

    def deli_args = ""
    if (params.debug) {
        deli_args += " --debug"
    }
    if (params.deli_data_dir) {
        deli_args += " --deli-data-dir '${params.deli_data_dir}'"
    }
    if (params.config_file) {
        deli_args += " --config-file '${params.config_file}'"
    }

    extract = ExtractSequenceFiles(selection_file_path)
    def final_prefix = params.prefix ?: null

    prefix_ch = extract.selection_id
        .splitText()
        .map { it.trim() }
        .filter { it }
        .map { final_prefix ?: it }
        .first()

    // CRITICAL FIX: Use the FASTQ Path from the input channel, NOT from files.txt
    // The files.txt contains GCS paths which won't stage properly in downstream tasks
    fastq_chunks = fastq_files.splitFastq(by: params.chunk_size, file: true)

    decoded = DecodeChunk(fastq_chunks, selection_file_path, prefix_ch, Channel.value(deli_args))

    collected_decodes = CollectDecodeChunks(
        decoded.decoded_tsv.collect(),
        prefix_ch,
        Channel.value(deli_args)
    )

    merged_stats = MergeDecodeStatistics(
        decoded.decode_stats.collect(),
        selection_file_path,
        prefix_ch,
        Channel.value(deli_args)
    )

    WriteDecodeReport(
        merged_stats.merged_stats,
        selection_file_path,
        prefix_ch,
        Channel.value(deli_args)
    )

    count_chunks = collected_decodes.ndjson
        .splitText(by: 500_000, file: true)

    counts = CountChunk(
        count_chunks,
        prefix_ch,
        Channel.value(deli_args)
    )

    collected_counts = CollectCountChunks(
        counts.counted.collect(),
        prefix_ch
    )

    SummarizeDecodeRun(
        collected_counts.merged_counts,
        merged_stats.merged_stats,
        prefix_ch,
        Channel.value(deli_args)
    )

    emit:
    counts  = CollectCountChunks.out.merged_counts
    summary = SummarizeDecodeRun.out.final_stats
    report  = WriteDecodeReport.out.report
}


