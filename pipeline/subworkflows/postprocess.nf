#!/usr/bin/env nextflow

/**
 * ==============================================================================
 * POSTPROCESS SUBWORKFLOW
 * ==============================================================================
 * Processes DELi counts through:
 * 1. Deduplication (removes duplicate compounds)
 * 2. Enrichment analysis
 *
 * Input:
 * - counts.parquet from DELI (or pre-existing)
 *
 * Output:
 * - singletons.parquet  (compound-level enrichment metrics)
 * - disynthon_*.parquet (disynthon-level enrichment metrics, one file per cycle pair)
 *
 * GCS-specific notes:
 * - Input parquet staged from GCS to container automatically
 * - All Python scripts read from local work dir after staging
 * - Output files published to out_dir (GCS bucket if configured)
 * ==============================================================================
 */

nextflow.enable.dsl = 2

// ============================================================================
// DEDUPLICATE PROCESS
// ============================================================================
// Removes duplicate compounds from DELi counts
// Calls Python script: src/deliver/postprocess/deduplicate.py

process BUILD_LIBRARY_DICT {
    publishDir "${params.out_dir}", mode: 'copy'

    output:
    path "library_dict.json"

    script:
    """
    python ${params.deliver_src_dir}/deliver/postprocess/build_library_dict.py \
        --deli-data-dir '${params.deli_data_dir}' \
        --output library_dict.json
    """

    stub:
    """
    touch library_dict.json
    """
}

process NORMALIZE {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path counts_parquet

    output:
    path "normalized.parquet"

    script:
    """
    python ${params.deliver_src_dir}/deliver/postprocess/normalize.py \
        --input  ${counts_parquet} \
        --output normalized.parquet
    """

    stub:
    """
    touch normalized.parquet
    """
}

process NORMALIZE_CUSTOM {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path counts_parquet

    output:
    path "normalized.parquet"

    script:
    def cols = params.counts
    def compound_args
    if (cols.compound_col) {
        def nc = cols.num_cycles ? "--num-cycles ${cols.num_cycles}" : ""
        compound_args = "--compound-col '${cols.compound_col}' ${nc}"
    } else if (cols.bb_ids_col) {
        compound_args = "--library-col '${cols.library_col}' --bb-ids-col '${cols.bb_ids_col}'"
    } else {
        compound_args = "--library-col '${cols.library_col}' --cycle-cols ${cols.cycle_cols.join(' ')}"
    }
    def raw_arg    = cols.raw_count_col ? "--raw-count-col '${cols.raw_count_col}'"   : ""
    def z_arg      = cols.z_score_col   ? "--z-score-col '${cols.z_score_col}'"       : ""
    def smiles_arg = cols.smiles_col    ? "--smiles-col '${cols.smiles_col}'"         : ""
    """
    python ${params.deliver_src_dir}/deliver/postprocess/normalize_custom.py \
        --input  ${counts_parquet} \
        --output normalized.parquet \
        --corrected-count-col '${cols.corrected_count_col}' \
        ${compound_args} \
        ${raw_arg} \
        ${z_arg} \
        ${smiles_arg}
    """

    stub:
    """
    touch normalized.parquet
    """
}

process DEDUPLICATE {
    tag "deduplicate"
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path counts_parquet
    // Parquet file from DELI (staged from GCS if needed)

    output:
    path "deduplicated.parquet", emit: dedup

    script:
    """
    python ${params.deliver_src_dir}/deliver/postprocess/deduplicate.py \
        --input  ${counts_parquet} \
        --output deduplicated.parquet \
        --on-duplicate-compound-id '${params.on_duplicate_compound_id}'
    """

    stub:
    """
    touch deduplicated.parquet
    """
}

process ADD_SMILES_LIB {
    tag "${lib_id}"

    input:
    tuple path(normalized_parquet), val(lib_id), val(smiles_path)

    output:
    path "${lib_id}_with_smiles.parquet", emit: smiles
    path "${lib_id}_smiles_report.parquet", emit: report

    script:
    def smiles_map   = groovy.json.JsonOutput.toJson([(lib_id): smiles_path])
    def compound_col = params.smiles.compound_col ?: "compound"
    def smiles_col   = params.smiles.smiles_col   ?: "SMILES"
    def max_missing  = params.smiles.max_missing_fraction ?: 0.01
    """
    echo '${smiles_map}' > smiles_map.json
    python ${params.deliver_src_dir}/deliver/postprocess/add_smiles.py \
        --input        ${normalized_parquet} \
        --smiles-map   smiles_map.json \
        --compound-col ${compound_col} \
        --smiles-col   ${smiles_col} \
        --library      ${lib_id} \
        --max-missing-fraction ${max_missing} \
        --output       ${lib_id}_with_smiles.parquet \
        --report       ${lib_id}_smiles_report.parquet
    """

    stub:
    """
    touch ${lib_id}_with_smiles.parquet
    touch ${lib_id}_smiles_report.parquet
    """
}

process MERGE_SMILES {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path normalized_parquet
    path partials
    path reports

    output:
    path "normalized.parquet", emit: normalized
    path "smiles_report.tsv", emit: report

    script:
    def smiles_col = params.smiles.smiles_col ?: "SMILES"
    """
    python ${params.deliver_src_dir}/deliver/postprocess/merge_smiles.py \
        --input         ${normalized_parquet} \
        --partials      ${partials} \
        --reports       ${reports} \
        --smiles-col    ${smiles_col} \
        --output        normalized.parquet \
        --report-output smiles_report.tsv
    """

    stub:
    """
    cp ${normalized_parquet} normalized.parquet
    touch smiles_report.tsv
    """
}

process SINGLETON {
    tag "singleton"
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path deduplicated_parquet
    path library_dict

    output:
    path "singletons.parquet"
    path "disynthon_*.parquet"

    script:
    """
    python ${params.deliver_src_dir}/deliver/postprocess/singleton.py \
        --input        ${deduplicated_parquet} \
        --library-dict ${library_dict} \
        --output       singletons.parquet

    python ${params.deliver_src_dir}/deliver/postprocess/disynthons.py \
        --input        ${deduplicated_parquet} \
        --library-dict ${library_dict} \
        --output-dir   .
    """

    stub:
    """
    touch singletons.parquet
    touch disynthon_AB.parquet
    """
}

process JOIN {
    tag "join"
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path singletons_parquet
    path disynthon_files

    output:
    path "enriched.parquet",            emit: enriched
    path "enriched_duplicates.parquet", emit: duplicates, optional: true

    script:
    """
    python ${params.deliver_src_dir}/deliver/postprocess/join.py \
        --input      ${singletons_parquet} \
        --disynthons ${disynthon_files} \
        --output     enriched.parquet
    """

    stub:
    """
    touch enriched.parquet
    """
}

process LABEL {
    tag "label"
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path enriched_parquet

    output:
    path "labeled.parquet",            emit: labeled
    path "labeled_duplicates.parquet", emit: duplicates, optional: true

    script:
    def modes = params.labeling.join(" ")
    """
    python ${params.deliver_src_dir}/deliver/postprocess/label.py \
        --input  ${enriched_parquet} \
        --modes  ${modes} \
        --output labeled.parquet
    """

    stub:
    """
    touch labeled.parquet
    """
}

// ============================================================================
// POSTPROCESS WORKFLOW
// ============================================================================

workflow POSTPROCESS {
    take:
    counts
    // Path channel: merged counts parquet from DELI (or pre-existing)

    main:
    def lib_dict_ch
    if (params.library_dict) {
        lib_dict_ch = Channel.fromPath(params.library_dict)
    } else {
        BUILD_LIBRARY_DICT()
        lib_dict_ch = BUILD_LIBRARY_DICT.out
    }

    log.info """
    ========================================
    POSTPROCESS: Deduplication & Enrichment
    ========================================
    Input counts: ${counts}
    ========================================
    """.stripIndent()

    // Step 1: Normalize (DELi format or external format with column mapping)
    def normalized_ch
    if (params.counts?.format == "external") {
        NORMALIZE_CUSTOM(counts)
        normalized_ch = NORMALIZE_CUSTOM.out
    } else {
        NORMALIZE(counts)
        normalized_ch = NORMALIZE.out
    }

    // Step 2: Add SMILES (optional) and Deduplicate
    // Skip SMILES join if SMILES is already in the file (counts.smiles_col)
    def has_embedded_smiles = params.counts?.format == "external" && params.counts?.smiles_col
    if (params.smiles && !has_embedded_smiles) {
        def smiles_ch = Channel.from(
            params.smiles.files.collect { lib_id, smiles_path -> [lib_id, smiles_path] }
        )
        ADD_SMILES_LIB(normalized_ch.combine(smiles_ch))
        MERGE_SMILES(normalized_ch, ADD_SMILES_LIB.out.smiles.collect(), ADD_SMILES_LIB.out.report.collect())
        DEDUPLICATE(MERGE_SMILES.out.normalized)
    } else {
        DEDUPLICATE(normalized_ch)
    }

    // Step 3: Enrichment analysis
    SINGLETON(DEDUPLICATE.out.dedup, lib_dict_ch)

    // Step 4: Join singletons and disynthons into a single enriched table
    JOIN(SINGLETON.out[0], SINGLETON.out[1])

    // Step 5: Label compounds (optional — skipped when params.labeling is null)
    if (params.labeling) {
        LABEL(JOIN.out.enriched)
    }

    emit:
    enriched     = JOIN.out.enriched
    singletons   = SINGLETON.out[0]
    disynthons   = SINGLETON.out[1]
    library_dict = lib_dict_ch
}
