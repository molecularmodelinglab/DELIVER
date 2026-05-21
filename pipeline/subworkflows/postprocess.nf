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
 * - enrichment.parquet (published to out_dir)
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
    python ${projectDir}/../src/deliver/postprocess/build_library_dict.py \
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
    python ${projectDir}/../src/deliver/postprocess/normalize.py \
        --input  ${counts_parquet} \
        --output normalized.parquet
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
    def deli_data_arg = params.deli_data_dir 
        ? "--deli-data-dir '${params.deli_data_dir}'" 
        : ""

    """
    echo "========================================"
    echo "DEDUPLICATE: Removing duplicate compounds"
    echo "========================================"
    echo "Input: ${counts_parquet}"
    
    # Verify input file exists
    if [[ ! -f "${counts_parquet}" ]]; then
        echo "ERROR: Input parquet not found: ${counts_parquet}"
        ls -lah
        exit 1
    fi
    
    echo "Input file size: \$(du -h ${counts_parquet} | cut -f1)"

    python ${params.deliver_src_dir}/deliver/postprocess/deduplicate.py \\
        --input  ${counts_parquet} \\
        --output deduplicated.parquet \\
        ${deli_data_arg}

    # Verify output
    if [[ -f deduplicated.parquet ]]; then
        echo "Output file size: \$(du -h deduplicated.parquet | cut -f1)"
    else
        echo "ERROR: Deduplication failed, output not created"
        exit 1
    fi
    """

    stub:
    """
    touch deduplicated.parquet
    """
}

process ADD_SMILES {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path normalized_parquet

    output:
    path "normalized.parquet"

    script:
    def smiles_map   = groovy.json.JsonOutput.toJson(params.smiles.files)
    def compound_col = params.smiles.compound_col ?: "compound"
    def smiles_col   = params.smiles.smiles_col   ?: "SMILES"
    """
    echo '${smiles_map}' > smiles_map.json
    python ${projectDir}/../src/deliver/postprocess/add_smiles.py \
        --input        ${normalized_parquet} \
        --smiles-map   smiles_map.json \
        --compound-col ${compound_col} \
        --smiles-col   ${smiles_col} \
        --output       normalized.parquet
    """

    stub:
    """
    cp ${normalized_parquet} normalized.parquet
    """
}

process ENRICHMENT {
    tag "enrichment"
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path deduplicated_parquet
    path library_dict

    output:
    path "enrichment.parquet"
    path "disynthons_*.parquet"

    script:
    """
    python ${projectDir}/../src/deliver/postprocess/enrichment.py \
        --input        ${deduplicated_parquet} \
        --library-dict ${library_dict} \
        --output       enrichment.parquet

    python ${projectDir}/../src/deliver/postprocess/disynthons.py \
        --input        ${deduplicated_parquet} \
        --library-dict ${library_dict} \
        --output-dir   .
    """

    stub:
    """
    touch enrichment.parquet
    touch disynthons_AB.parquet
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
    BUILD_LIBRARY_DICT()

    log.info """
    ========================================
    POSTPROCESS: Deduplication & Enrichment
    ========================================
    Input counts: ${counts}
    ========================================
    """.stripIndent()

    // Step 1: Normalize
    NORMALIZE(counts)

    // Step 2: Add SMILES (optional) and Deduplicate
    if (params.smiles) {
        ADD_SMILES(NORMALIZE.out)
        DEDUPLICATE(ADD_SMILES.out)
    } else {
        DEDUPLICATE(NORMALIZE.out)
    }

    // Step 3: Enrichment analysis
    ENRICHMENT(DEDUPLICATE.out.dedup, BUILD_LIBRARY_DICT.out)

    emit:
    enrichment   = ENRICHMENT.out[0]
    disynthons   = ENRICHMENT.out[1]
    library_dict = BUILD_LIBRARY_DICT.out
}
