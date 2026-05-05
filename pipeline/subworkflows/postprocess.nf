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

// ============================================================================
// ENRICHMENT PROCESS
// ============================================================================
// Performs enrichment analysis on deduplicated compounds
// Calls Python script: src/deliver/postprocess/enrichment.py

process ENRICHMENT {
    tag "enrichment"
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path deduplicated_parquet
    // Deduplicated parquet file (staged from GCS if needed)

    output:
    path "enrichment.parquet", emit: enrichment

    script:
    def deli_data_arg = params.deli_data_dir 
        ? "--deli-data-dir '${params.deli_data_dir}'" 
        : ""

    """
    echo "========================================"
    echo "ENRICHMENT: Performing enrichment analysis"
    echo "========================================"
    echo "Input: ${deduplicated_parquet}"
    
    # Verify input file exists
    if [[ ! -f "${deduplicated_parquet}" ]]; then
        echo "ERROR: Input parquet not found: ${deduplicated_parquet}"
        ls -lah
        exit 1
    fi
    
    echo "Input file size: \$(du -h ${deduplicated_parquet} | cut -f1)"

    python ${params.deliver_src_dir}/deliver/postprocess/enrichment.py \\
        --input  ${deduplicated_parquet} \\
        --output enrichment.parquet \\
        ${deli_data_arg}

    # Verify output
    if [[ -f enrichment.parquet ]]; then
        echo "Output file size: \$(du -h enrichment.parquet | cut -f1)"
    else
        echo "ERROR: Enrichment analysis failed, output not created"
        exit 1
    fi
    """

    stub:
    """
    touch enrichment.parquet
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

    // Step 1: Deduplicate
    DEDUPLICATE(counts)

    // Step 2: Enrichment analysis
    ENRICHMENT(DEDUPLICATE.out.dedup)

    emit:
    results      = ENRICHMENT.out.enrichment
    library_dict = BUILD_LIBRARY_DICT.out
}
