#!/usr/bin/env nextflow

/**
 * ==============================================================================
 * DELIVER PIPELINE — Main Workflow
 * ==============================================================================
 * Supports:
 * - Local execution (params.profile=local)
 * - HPC execution (params.profile=hpc)
 * - GCP with GCS buckets (params.profile=gcp)
 *
 * Input modes:
 * 1. FASTQ files (read_1, read_2) → preprocess → deli → postprocess
 * 2. Pre-counted parquet (counts_file) → postprocess only
 *
 * GCS Notes:
 * - Input paths from GCS: gs://bucket/path/file.fastq.gz
 * - Work dir from GCS: gs://bucket/work/
 * - Output dir to GCS: gs://bucket/results/
 * - Nextflow automatically stages files from GCS to container
 * ==============================================================================
 */

nextflow.enable.dsl = 2

// Include subworkflows
include { PREPROCESS  } from './subworkflows/preprocess.nf'
include { DELI        } from './subworkflows/deli.nf'
include { POSTPROCESS } from './subworkflows/postprocess.nf'


workflow {
    // Input validation
    has_fastq  = params.read_1 as boolean
    has_counts = params.counts_file as boolean

    if (has_fastq && has_counts) {
        error("Provide either read_1 or counts_file, not both")
    } else if (!has_fastq && !has_counts) {
        error("Provide either read_1 (FASTQ input) or counts_file (counts.parquet input)")
    }

    if (has_fastq) {

        PREPROCESS()

        fastq_uri = PREPROCESS.out.fastq.map { it.toUriString() }
        DELI(
            PREPROCESS.out.fastq,  // path - for splitFastq
            fastq_uri              // val  - for YAML
        )
        
        POSTPROCESS(DELI.out.counts)

    } else if (has_counts) {
        // ====================================================================
        // Path 2: Pre-counted parquet → Postprocess only
        // ====================================================================
        
        // Load pre-existing counts parquet from GCS or local
        counts_ch = Channel.fromPath(params.counts_file)
        
        POSTPROCESS(counts_ch)
    }
}
