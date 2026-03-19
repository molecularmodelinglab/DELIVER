nextflow.enable.dsl = 2

// Postprocessing subworkflow.
// Nextflow wrapper processes around Python scripts in src/deliver/postprocess/.
// Each script can also be run standalone for debugging (see src/deliver/postprocess/).

process DEDUPLICATE {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path counts_parquet

    output:
    path "deduplicated.parquet"

    script:
    def deli_data_arg = params.deli_data_dir ? "--deli-data-dir '${params.deli_data_dir}'" : ""
    """
    python ${projectDir}/../src/deliver/postprocess/deduplicate.py \
        --input  ${counts_parquet} \
        --output deduplicated.parquet \
        ${deli_data_arg}
    """

    stub:
    """
    touch deduplicated.parquet
    """
}

process ENRICHMENT {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path deduplicated_parquet

    output:
    path "enrichment.parquet"

    script:
    def deli_data_arg = params.deli_data_dir ? "--deli-data-dir '${params.deli_data_dir}'" : ""
    """
    python ${projectDir}/../src/deliver/postprocess/enrichment.py \
        --input  ${deduplicated_parquet} \
        --output enrichment.parquet \
        ${deli_data_arg}
    """

    stub:
    """
    touch enrichment.parquet
    """
}

workflow POSTPROCESS {
    take:
    counts  // path channel — merged counts parquet from DELI

    main:
    DEDUPLICATE(counts)
    ENRICHMENT(DEDUPLICATE.out)

    emit:
    results = ENRICHMENT.out
}
