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
 * 2. Pre-merged FASTQ (merged_fastq) → deli → postprocess (skips preprocess)
 * 3. Pre-counted parquet (counts.file) → postprocess only
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

// Base names of input files (list or comma-separated string), for the decode YAML.
def input_names(v) {
    if (!v) return []
    def items = v instanceof List ? v : v.toString().split(',')
    return items.collect { it.toString().trim().tokenize('/').last() }.findAll { it }
}

workflow {
    // Input validation — exactly one entry point must be set
    has_fastq  = params.read_1 as boolean
    has_merged = params.merged_fastq as boolean
    has_counts = params.counts as boolean

    def n_modes = [has_fastq, has_merged, has_counts].count { it }
    if (n_modes != 1) {
        error("Provide exactly one of: read_1 (raw FASTQ input), merged_fastq (pre-merged FASTQ, skips preprocess), or counts (counts parquet input) — got ${n_modes}")
    }

    if (has_fastq) {

        PREPROCESS()

        DELI(
            PREPROCESS.out.fastq,   // one FASTQ per lane
            Channel.value(input_names(params.read_1) + input_names(params.read_2))
        )

        POSTPROCESS(DELI.out.counts)

    } else if (has_merged) {
        // ====================================================================
        // Path 2: Pre-merged FASTQ → DELI → Postprocess (PREPROCESS skipped)
        // ====================================================================
        // Recovery/re-run entry point: feed an already-merged (or single-end
        // already-decompressed) FASTQ straight into decoding — e.g. a
        // FASTP_MERGE output stranded in an old work dir. Channel.fromPath
        // preserves gs:// URIs (file() at workflow scope strips the scheme —
        // see the same note in preprocess.nf).
        DELI(
            Channel.fromPath(params.merged_fastq),
            Channel.value(input_names(params.merged_fastq))
        )

        POSTPROCESS(DELI.out.counts)

    } else if (has_counts) {
        // ====================================================================
        // Path 3: Pre-counted parquet → Postprocess only
        // ====================================================================
        if (!params.counts.format) {
            error("counts.format is required: set to \"deli\" or \"external\"")
        }
        if (params.counts.format == "external") {
            def c = params.counts
            // Exactly one compound identity mode
            def compound_modes = [c.compound_col, c.bb_ids_col, c.cycle_cols].count { it }
            if (compound_modes == 0) {
                error("counts (external format): specify compound identity via compound_col, library_col + bb_ids_col, or library_col + cycle_cols")
            }
            if (compound_modes > 1) {
                error("counts (external format): specify only one compound identity mode (compound_col, bb_ids_col, or cycle_cols)")
            }
            if (!c.corrected_count_col) {
                error("counts (external format): corrected_count_col is required")
            }
        }
        counts_ch = Channel.fromPath(params.counts.file)
        POSTPROCESS(counts_ch)
    }
}
