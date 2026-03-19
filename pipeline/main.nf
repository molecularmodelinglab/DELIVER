#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { PREPROCESS  } from './subworkflows/preprocess'
include { DELI        } from './subworkflows/deli'
include { POSTPROCESS } from './subworkflows/postprocess'

workflow {
    has_fastq  = params.forward_reads as boolean
    has_counts = params.counts_file   as boolean

    if (has_fastq && has_counts) {
        error("Provide either forward_reads or counts_file, not both")
    } else if (!has_fastq && !has_counts) {
        error("Provide either forward_reads (FASTQ input) or counts_file (counts.parquet input)")
    } else if (has_fastq) {
        PREPROCESS()
        DELI(PREPROCESS.out.fastq.map { [it.toAbsolutePath().toString()] })
        POSTPROCESS(DELI.out.counts)
    } else {
        POSTPROCESS(Channel.fromPath(params.counts_file))
    }
}
