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

    stub:
    def yaml_name = "${params.selection_id}_${params.target_id}_${params.date_ran}.yaml"
    """
    touch ${yaml_name}
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

    stub:
    """
    echo stub > selection_id.txt
    echo /dev/null > files.txt
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
    path "${prefix}_${fastq_chunk.simpleName}_decoded.tsv", emit: decoded_tsv
    path "${prefix}_${fastq_chunk.simpleName}_decode_statistics.json", emit: decode_stats
    path "${prefix}_${fastq_chunk.simpleName}_deli.log", emit: deli_log
    path "${prefix}_${fastq_chunk.simpleName}_failed_decoding.tsv", emit: failed_tsv, optional: true

    script:
    def fastq_info_flag  = params.save_fastq_info ? "--save-fastq-info" : ""
    def save_failed_flag = params.save_failed      ? "--save-failed"     : ""
    """
    deli ${deli_args} decode run \
        "${selection_file}" \
        "${fastq_chunk}" \
        --out-dir ./ \
        --prefix "${prefix}_${fastq_chunk.simpleName}" \
        --skip-report \
        ${fastq_info_flag} \
        ${save_failed_flag}

    mv deli.log ${prefix}_${fastq_chunk.simpleName}_deli.log
    """

    stub:
    """
    touch ${prefix}_${fastq_chunk.simpleName}_decoded.tsv
    echo '{}' > ${prefix}_${fastq_chunk.simpleName}_decode_statistics.json
    touch ${prefix}_${fastq_chunk.simpleName}_deli.log
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
    // NOTE: DELi's `decode collect --compress` is present in the CLI but hard-disabled
    // in this installed version (polars sink_ndjson doesn't support compression yet —
    // it exits 1 if passed), so this output stays uncompressed until that's fixed upstream.
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
// DEBUG MERGE PROCESSES
// ============================================================================

process MergeDebugLogs {
    publishDir "${params.out_dir}/debug", mode: 'copy'

    input:
    path("*_deli.log", arity: '1..*')
    val prefix

    output:
    path "${prefix}_deli.log"

    script:
    """
    cat *_deli.log > ${prefix}_deli.log
    """

    stub:
    """
    touch ${prefix}_deli.log
    """
}

process MergeDebugDecoded {
    publishDir "${params.out_dir}/debug", mode: 'copy'

    input:
    path("*_decoded.tsv", arity: '1..*')
    val prefix

    output:
    path "${prefix}_debug_decoded.tsv"

    script:
    """
    awk 'FNR==1 && NR!=1{next}1' *_decoded.tsv > ${prefix}_debug_decoded.tsv
    """

    stub:
    """
    touch ${prefix}_debug_decoded.tsv
    """
}

process MergeDebugFailed {
    publishDir "${params.out_dir}/debug", mode: 'copy'

    input:
    path("*_failed_decoding.tsv", arity: '1..*')
    val prefix

    output:
    path "${prefix}_failed_decoding.tsv"

    script:
    """
    awk 'FNR==1 && NR!=1{next}1' *_failed_decoding.tsv > ${prefix}_failed_decoding.tsv
    """

    stub:
    """
    touch ${prefix}_failed_decoding.tsv
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
    fastq_chunks = fastq_files.splitFastq(by: params.chunk_size, file: true, compress: true)

    decoded = DecodeChunk(fastq_chunks, selection_file_path, prefix_ch, Channel.value(deli_args))

    if (params.debug) {
        MergeDebugLogs(decoded.deli_log.collect(), prefix_ch)
    }
    if (params.save_fastq_info) {
        MergeDebugDecoded(decoded.decoded_tsv.collect(), prefix_ch)
    }
    if (params.save_failed) {
        MergeDebugFailed(decoded.failed_tsv.collect(), prefix_ch)
    }

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
        .splitText(by: 500_000, file: true, compress: true)

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


