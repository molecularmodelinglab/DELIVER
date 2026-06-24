#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

/*
 * Rarefaction analysis on already-decoded reads.
 *
 * Subsamples *_decoded.tsv chunk files from a Nextflow work directory at
 * different fractions, then runs `deli decode collect` + `deli decode count`
 * on each fraction in parallel.
 *
 * Sampling is at the chunk-file level (fast; no need to load 300M rows).
 * With ~400 chunks the approximation is accurate enough for saturation curves.
 *
 * Usage:
 *   nextflow run scripts/rarefaction/rarefaction.nf \
 *     -profile longleaf \
 *     -c scripts/rarefaction/rarefaction.config \
 *     -w          /path/to/rarefaction/work \
 *     --decoded-dir /path/to/original/deliver/work \
 *     --out-dir   /path/to/output \
 *     [--fractions "0.05,0.10,0.25,0.50,0.75,0.90,1.00"] \
 *     [--seed 42] \
 *     [--deli-data-dir /path/to/deli_data]
 */

params.decoded_dir      = null
params.out_dir       = null
params.fractions     = "0.05,0.10,0.25,0.50,0.75,0.90,1.00"
params.seed          = 42
params.deli_data_dir = null


// ── helpers ──────────────────────────────────────────────────────────────────

def parseFractions(raw) {
    if (raw instanceof List) return raw.collect { it as double }
    return raw.toString().split(',').collect { it.trim() as double }
}

def fractionLabel(frac) {
    String.format('%03d', (frac * 100).toInteger()) + 'pct'
}


// ── processes ─────────────────────────────────────────────────────────────────

// Finds all *_decoded.tsv files in the work dir; runs once on the executor.
process FIND_DECODED {
    output:
    path "decoded_paths.txt"

    script:
    """
    find ${params.decoded_dir} -type f -name '*_decoded.tsv' | sort > decoded_paths.txt
    echo "Found \$(wc -l < decoded_paths.txt) decoded TSV files"
    """
}


// For one fraction: randomly selects chunk files and runs deli decode collect.
// Uses absolute paths from the shared /work filesystem — no file staging needed.
process COLLECT_SUBSAMPLE {
    tag "${label}"
    publishDir "${params.out_dir}/${label}", mode: 'copy', pattern: '*.ndjson'

    input:
    tuple val(fraction), val(label), path(paths_file)

    output:
    tuple val(fraction), val(label), path("${label}_collected.ndjson")

    script:
    def deli_data = params.deli_data_dir ? "--deli-data-dir '${params.deli_data_dir}'" : ""
    """
    python3 - << 'PYEOF'
import random, subprocess, sys

paths = [l.strip() for l in open('${paths_file}') if l.strip()]
n = max(1, round(len(paths) * ${fraction}))
rng = random.Random(${params.seed})
sampled = sorted(rng.sample(paths, n))
print(f"[${label}] selected {n}/{len(paths)} chunks", flush=True)

cmd = ['deli']
ddi = '${params.deli_data_dir}'
if ddi and ddi != 'null':
    cmd += ['--deli-data-dir', ddi]
cmd += ['decode', 'collect'] + sampled + ['--out-loc', '${label}_collected.ndjson']
subprocess.run(cmd, check=True)
PYEOF
    """

    stub:
    """
    echo '{"library_id":"L01","bb_ids":"1,2,3","umi_counts":[]}' > ${label}_collected.ndjson
    """
}


// Runs deli decode count on the full per-fraction ndjson (no splitting).
// Fractions run in parallel, so wall time ≈ time for the largest fraction.
process COUNT_FRACTION {
    tag "${label}"
    publishDir "${params.out_dir}/${label}", mode: 'copy'

    input:
    tuple val(fraction), val(label), path(ndjson)

    output:
    tuple val(fraction), val(label), path("${label}_counts.parquet")

    script:
    def deli_data = params.deli_data_dir ? "--deli-data-dir '${params.deli_data_dir}'" : ""
    """
    deli ${deli_data} decode count \
        "${ndjson}" \
        --out-loc "${label}_counts.parquet" \
        --output-format parquet \
        --cluster-umis \
        --keep-raw-count \
        --keep-dedup-count
    """

    stub:
    """
    touch ${label}_counts.parquet
    """
}


// Reads all per-fraction counts.parquet files and writes a summary CSV.
process RAREFACTION_SUMMARY {
    publishDir "${params.out_dir}", mode: 'copy'

    input:
    path parquet_files

    output:
    path "rarefaction_summary.csv"

    script:
    """
    #!/usr/bin/env python3
    import polars as pl
    from pathlib import Path

    rows = []
    for f in sorted(Path('.').glob('*_counts.parquet')):
        label = f.stem.replace('_counts', '')
        fraction = int(label.replace('pct', '')) / 100
        df = pl.read_parquet(f)
        rows.append({
            'fraction':    fraction,
            'label':       label,
            'n_compounds': len(df),
            'total_reads': int(df['raw_count'].sum())   if 'raw_count'   in df.columns else None,
            'dedup_reads': int(df['dedup_count'].sum()) if 'dedup_count' in df.columns else None,
        })

    summary = pl.DataFrame(rows).sort('fraction')
    summary.write_csv('rarefaction_summary.csv')
    print(summary)
    """

    stub:
    """
    echo 'fraction,label,n_compounds,total_reads,dedup_reads' > rarefaction_summary.csv
    """
}


// ── workflow ──────────────────────────────────────────────────────────────────

workflow {
    if (!params.decoded_dir) error "Provide --decoded-dir (work directory of the original DELIVER run, containing *_decoded.tsv files)"
    if (!params.out_dir)  error "Provide --out-dir"

    fractions = parseFractions(params.fractions)

    paths_file = FIND_DECODED()

    fractions_ch = Channel
        .from(fractions)
        .map { frac -> [frac, fractionLabel(frac)] }

    collect_input = fractions_ch.combine(paths_file)

    collected = COLLECT_SUBSAMPLE(collect_input)
    counted   = COUNT_FRACTION(collected)

    all_parquets = counted.map { _frac, _label, parquet -> parquet }.collect()
    RAREFACTION_SUMMARY(all_parquets)
}
