# DELIVER Pipeline — Detailed Walkthrough

End-to-end description of the Nextflow pipeline starting from `pipeline/main.nf`.

The DELi codebase referenced below is the `patch` branch:
<https://github.com/Popov-Lab-UNC/DELi/tree/patch>.

---

## 1. Top-level entry point — `pipeline/main.nf`

`main.nf` decides between two run modes based on `params.yml`:

| Condition                  | Path taken                                        |
|----------------------------|---------------------------------------------------|
| `params.read_1` set        | `PREPROCESS → DELI → POSTPROCESS` (full pipeline) |
| `params.counts` set        | `POSTPROCESS` only (skip decoding)                |
| both set / neither set     | hard error                                        |

When `params.counts` is set, `main.nf` also validates the format block:
- `counts.format` must be `"deli"` or `"external"` — error otherwise.
- For `"external"`: exactly one compound identity mode must be specified
  (`compound_col`, `bb_ids_col`, or `cycle_cols`), and `corrected_count_col` is required.

The full path wires the subworkflows together:

```
PREPROCESS()  →  fastq channel
DELI(fastq, fastq_uri)  →  counts.parquet, decode_summary.json, decode_report.html
POSTPROCESS(counts.parquet)  →  enriched.parquet  [+ labeled.parquet]
```

---

## 2. PREPROCESS subworkflow — `pipeline/subworkflows/preprocess.nf`

**Goal:** turn one or more raw FASTQ files (local or `gs://`) into a single
uncompressed `merged.fastq` ready for DELi.

| Process       | Input                                    | Output                                          | Tool     |
|---------------|------------------------------------------|-------------------------------------------------|----------|
| `CONCAT`      | list of fastq[.gz] per read type         | `R1.fastq[.gz]` / `R2.fastq[.gz]`              | `cat`    |
| `FASTP_MERGE` | concatenated R1 + R2                     | `merged.fastq`, `fastp.html`, `fastp.json`      | `fastp`  |
| `DECOMPRESS`  | concatenated single-end R1               | `merged.fastq`                                  | `gunzip` |

Logic:
- **Paired-end** (`read_2` set): `CONCAT` runs twice (R1, R2) → `FASTP_MERGE` merges with overlap correction.
- **Single-end**: `CONCAT` once on R1 → `DECOMPRESS` produces `merged.fastq`.
- Only `FASTP_MERGE` publishes (`${out_dir}/qc/fastp.html` + `fastp.json`).

**Emits:** `fastq` — path to `merged.fastq`.

---

## 3. DELI subworkflow — `pipeline/subworkflows/deli.nf`

Takes the merged FASTQ and produces a compound counts table plus run statistics
and an HTML report. The FASTQ is chunked, fanned out to many parallel decode jobs,
aggregated, then chunked again for parallel counting.

### 3.1 `GenerateDecodeYaml`

Writes the DELi "selection file" (`${selection_id}_${target_id}_${date_ran}.yaml`)
containing selection metadata, sequence file paths, library list, and decode settings.
Published to `out_dir`.

### 3.2 `ExtractSequenceFiles`

Reads the YAML and writes `selection_id.txt` (used for output file prefixes)
and `files.txt` (informational sequence file list).

### 3.3 `splitFastq` → `DecodeChunk` (parallel)

The merged FASTQ is split into chunks of `params.chunk_size` reads (default 1,000,000).
Each chunk runs:

```
deli decode run <selection.yaml> <chunk.fastq> --out-dir ./ --prefix ... --skip-report
```

Outputs per chunk: `*_decoded.tsv` (per-read decode results) + `*_decode_statistics.json`.

### 3.4 `CollectDecodeChunks`

```
deli decode collect *_decoded.tsv --out-loc ${prefix}_collected.ndjson
```

Folds all per-read TSVs into NDJSON records grouped by `(library_id, bb_ids)`.

### 3.5 `MergeDecodeStatistics`

```
deli decode merge-stats *_decode_statistics.json --out-loc ${prefix}_decode_stats.json
```

Published to `out_dir`.

### 3.6 `WriteDecodeReport`

```
deli decode report ${prefix}_decode_stats.json --out-loc ${prefix}_decode_report.html
```

Published to `out_dir`.

### 3.7 `splitText` → `CountChunk` (parallel)

The collected NDJSON is split into chunks of 500,000 lines. Each chunk runs:

```
deli decode count <chunk.ndjson> --output-format parquet --cluster-umis \
    --keep-raw-count --keep-dedup-count
```

`--cluster-umis` deduplicates UMIs to estimate molecule counts.
`--keep-raw-count` / `--keep-dedup-count` retain intermediate count columns.

### 3.8 `CollectCountChunks`

A Polars script (not DELi) that concatenates all counted parquets:

```python
pl.scan_parquet(files).sink_parquet("${prefix}_counts.parquet")
```

Published to `out_dir`. **This is the counts table fed to POSTPROCESS.**

### 3.9 `SummarizeDecodeRun`

```
deli decode summarize ${prefix}_counts.parquet ${prefix}_decode_stats.json \
    --out-loc ${prefix}_decode_summary.json
```

Published to `out_dir`.

### DELI input/output summary

| Input                                               | Source                              |
|-----------------------------------------------------|-------------------------------------|
| Merged FASTQ                                        | `PREPROCESS.out.fastq`              |
| Selection metadata                                  | `params.yml`                        |
| Library list                                        | `params.libraries`                  |
| Decode settings                                     | `params.yml` decode-settings block  |
| DELi reference data (library JSONs, bb tables)     | `params.deli_data_dir`              |

| Output (published to `out_dir`)                     |
|-----------------------------------------------------|
| `${selection_id}_${target_id}_${date_ran}.yaml`     |
| `${prefix}_decode_stats.json`                       |
| `${prefix}_decode_report.html`                      |
| `${prefix}_counts.parquet`  ← also fed to POSTPROCESS |
| `${prefix}_decode_summary.json`                     |

---

## 4. POSTPROCESS subworkflow — `pipeline/subworkflows/postprocess.nf`

Runs after DELI (or directly from a pre-existing counts parquet). All steps call
small Python scripts from `src/deliver/postprocess/`.

### Step 1 — BUILD_LIBRARY_DICT (or pre-computed)

If `params.library_dict` is set, this step is skipped entirely and the provided JSON
is used directly. This is useful when running in counts mode on a machine without DELi
data — the file can be generated once from any machine that has `deli_data_dir` and
reused across runs.

Otherwise:

```
build_library_dict.py --deli-data-dir <deli_data_dir> --output library_dict.json
```

Reads DELi library definitions from `deli_data_dir` and writes a JSON mapping each
library ID to its per-cycle building block counts, e.g.:

```json
{"L01": {"A": 100, "B": 200, "C": 150}, "L02": {...}}
```

Used by SINGLETON and DISYNTHONS to compute expected compound space sizes.
Published to `out_dir`.

### Step 2 — NORMALIZE or NORMALIZE_CUSTOM

Converts the raw counts parquet into a standard internal schema:

| Column          | Description                              |
|-----------------|------------------------------------------|
| `compound_id`   | `library_id-bbA-bbB-bbC` string         |
| `library_id`    | Library identifier                       |
| `A`, `B`, `C`   | Individual building block IDs per cycle  |
| `corrected_count` | UMI-corrected count (primary metric)  |
| `raw_reads`     | Raw read count (optional)                |
| `z_score`       | Pre-supplied z-score (optional, carried through) |
| `SMILES`        | SMILES string (optional, carried through) |

**`NORMALIZE`** (DELi format, `counts.format: "deli"`): reads `library_id`, `bb_ids`,
`count` / `raw_count` columns from DELi output and reshapes to standard schema.

**`NORMALIZE_CUSTOM`** (external format, `counts.format: "external"`): maps
user-specified column names to the standard schema. Compound identity can be
supplied in three ways (set exactly one in `params.counts`):

| Mode | Params |
|------|--------|
| (a) Pre-formatted compound ID | `compound_col` (+ optional `num_cycles` if library name contains `-`) |
| (b) Library + comma-separated bb IDs | `library_col` + `bb_ids_col` |
| (c) Library + individual cycle columns | `library_col` + `cycle_cols` |

Published to `out_dir` as `normalized.parquet`.

### Step 2b — ADD_SMILES_LIB + MERGE_SMILES (optional)

Runs only when `params.smiles` is set and SMILES are not already embedded in the
counts file (`counts.smiles_col` not set).

`ADD_SMILES_LIB` runs in parallel — one job per library in `params.smiles.files`.
Each job performs a DuckDB predicate-pushdown join against a sorted enumerated
parquet to look up SMILES by compound ID. Results are merged back into a single
`normalized.parquet` with a `SMILES` column added.

### Step 3 — DEDUPLICATE

```
deduplicate.py --input normalized.parquet --output deduplicated.parquet \
    --on-duplicate-compound-id <fail|sum>
```

Removes or merges duplicate `compound_id` rows.

| Mode | Behaviour |
|------|-----------|
| `fail` (default) | Aborts with an error listing duplicate IDs |
| `sum` | Merges duplicates by summing `corrected_count` and `raw_reads`; if a pre-supplied `z_score` is present, combines it via Stouffer's method (`sum(z) / sqrt(n)`) — a pragmatic approximation, since the correct approach would be to recompute the z-score from the pooled count |

If a `SMILES` column is present, all duplicate rows for the same `compound_id` must
share the same SMILES — fails loudly otherwise, regardless of mode.

Published to `out_dir` as `deduplicated.parquet`.

### Step 4 — SINGLETON (runs two scripts)

```
singleton.py   --input deduplicated.parquet --library-dict library_dict.json --output singletons.parquet
disynthons.py  --input deduplicated.parquet --library-dict library_dict.json --output-dir .
```

**`singleton.py`** computes per-compound enrichment metrics:

| Output column              | Description |
|----------------------------|-------------|
| `z_score_lib_normalized`   | Binomial z-score relative to library compound space |
| `z_score_global_normalized`| Binomial z-score relative to all libraries combined |
| `polyo`                    | PolyO score — Poisson-based enrichment metric |

**`disynthons.py`** aggregates to disynthon level (all pairs of cycles: AB, BC, AC, …)
and computes the same three metrics at the disynthon level. One file per cycle pair:
`disynthon_AB.parquet`, `disynthon_BC.parquet`, `disynthon_AC.parquet`, etc.
Each row also has `line_size`, `line_strength`, and `line_strength_std` (statistics over the
third cycle's building blocks within that disynthon).

Both PolyO and z-scores use `corrected_count` (UMI-corrected) throughout.
If a pre-supplied `z_score` column is present in the input, it is carried through
and per-compound z-scores are not recalculated.

All files published to `out_dir`.

### Step 5 — JOIN

```
join.py --input singletons.parquet --disynthons disynthon_*.parquet --output enriched.parquet
```

Left-joins disynthon metrics onto the singleton table, one set of columns per cycle
pair. Disynthon columns are prefixed with the pair name in lowercase:

| Singleton columns (unchanged)        | Added disynthon columns (example for AB) |
|--------------------------------------|------------------------------------------|
| `compound_id`, `library_id`, `A`, `B`, `C` | `ab_corrected_count_sum`       |
| `corrected_count`, `raw_reads`       | `ab_line_size`, `ab_line_strength`, `ab_line_strength_std` |
| `z_score_lib_normalized`, `z_score_global_normalized` | `ab_z_score_lib_normalized`, `ab_z_score_global_normalized` |
| `polyo`                              | `ab_polyo`                               |
| `SMILES` (if present)               | _(same for `bc_*`, `ac_*`, …)_           |

If a `SMILES` column is present and any SMILES appear more than once (same structure
reachable via different building block combinations), the duplicate rows are written
to `enriched_duplicates.parquet` sorted by SMILES.

Published to `out_dir`:
- `enriched.parquet` — always
- `enriched_duplicates.parquet` — only when SMILES duplicates exist

### Step 6 — LABEL (optional)

Runs only when `params.labeling` is set (non-null list of mode names).

```
label.py --input enriched.parquet --modes <mode1> [mode2 ...] --output labeled.parquet
```

Adds one `label_<mode>` boolean column per mode. Available modes:

| Mode | Positive criterion |
|------|--------------------|
| `count` | `corrected_count > 5` |
| `count_zscore_lib` | `corrected_count > 5` AND (`z_score_lib > 1` OR any disynthon `z_score_lib > 1`) |
| `count_zscore_global` | `corrected_count > 5` AND (`z_score_global > 1` OR any disynthon `z_score_global > 1`) |
| `count_polyo` | `corrected_count > 5` AND (`polyo > 4` OR any disynthon `polyo > 4`) |

Each mode validates that its required columns are present and fails with a clear
error message if they are not.

Same SMILES-duplicate logic as JOIN: if SMILES duplicates exist in the labeled
table, `labeled_duplicates.parquet` is written alongside.

Published to `out_dir`:
- `labeled.parquet` — always (when labeling is enabled)
- `labeled_duplicates.parquet` — only when SMILES duplicates exist

---

## 5. Published outputs summary

| File | Step | Notes |
|------|------|-------|
| `${sel_id}_${target_id}_${date}.yaml` | DELI | Generated DELi selection file |
| `${prefix}_decode_stats.json` | DELI | Merged per-chunk decode statistics |
| `${prefix}_decode_report.html` | DELI | Human-readable decode report |
| `${prefix}_counts.parquet` | DELI | Raw compound counts from DELi |
| `${prefix}_decode_summary.json` | DELI | Per-library decode summary |
| `library_dict.json` | POSTPROCESS | Library building block counts |
| `normalized.parquet` | POSTPROCESS | Counts in standard schema (+ SMILES if joined) |
| `deduplicated.parquet` | POSTPROCESS | After duplicate compound ID handling |
| `singletons.parquet` | POSTPROCESS | Compound-level enrichment metrics |
| `disynthon_AB.parquet`, … | POSTPROCESS | Disynthon-level enrichment metrics, one per cycle pair |
| `enriched.parquet` | POSTPROCESS | Singletons + disynthon metrics joined (wide table) |
| `enriched_duplicates.parquet` | POSTPROCESS | Enriched rows with duplicate SMILES, sorted by SMILES (optional) |
| `labeled.parquet` | POSTPROCESS | Enriched + `label_*` boolean columns (optional) |
| `labeled_duplicates.parquet` | POSTPROCESS | Labeled rows with duplicate SMILES, sorted by SMILES (optional) |
| `qc/fastp.html`, `qc/fastp.json` | PREPROCESS | Preprocessing QC (paired-end only) |

---

## 6. DELi CLI commands invoked

| Pipeline process        | DELi CLI                  | Purpose                                           |
|-------------------------|---------------------------|---------------------------------------------------|
| `DecodeChunk`           | `deli decode run`         | DNA reads → per-read decoded TSV + stats JSON     |
| `CollectDecodeChunks`   | `deli decode collect`     | Decoded TSVs → NDJSON aggregated by (lib, bb_ids) |
| `MergeDecodeStatistics` | `deli decode merge-stats` | Merge per-chunk stats JSONs                       |
| `WriteDecodeReport`     | `deli decode report`      | Stats JSON → HTML report                          |
| `CountChunk`            | `deli decode count`       | NDJSON chunk → counts parquet (UMI-clustered)     |
| `CollectCountChunks`    | *(Polars, not DELi)*      | Concatenate counts parquets via `pl.scan_parquet` |
| `SummarizeDecodeRun`    | `deli decode summarize`   | Counts + stats → per-library summary JSON         |
