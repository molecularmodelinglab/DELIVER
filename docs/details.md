# DELIVER Pipeline — Detailed Walkthrough

End-to-end analysis of the Nextflow pipeline starting from `pipeline/main.nf`, the
DELi decoding stage in detail (inputs, outputs and which DELi CLI is called at
each step), and what the final published artifact is.

The DELi codebase referenced below is the `patch` branch:
<https://github.com/Popov-Lab-UNC/DELi/tree/patch>.

---

## 1. Top-level entry point — `pipeline/main.nf`

`main.nf` decides between two run modes based on `params.yml`:

| Condition                  | Path taken                                         |
|----------------------------|----------------------------------------------------|
| `params.read_1` set        | `PREPROCESS → DELI → POSTPROCESS` (full pipeline)  |
| `params.counts_file` set   | `POSTPROCESS` only (skip decoding)                 |
| both set / neither set     | hard error                                         |

The full path wires the subworkflows together:

```
PREPROCESS()  →  fastq channel
DELI(fastq, fastq_uri)  →  counts (parquet), summary (json), report (html)
POSTPROCESS(DELI.out.counts)  →  enrichment.parquet
```

So the final published artifact is **`enrichment.parquet`** (see §5), produced by
`POSTPROCESS.ENRICHMENT` and copied to `${params.out_dir}`. Several intermediate
artifacts are also published along the way (`merged.fastq` + fastp QC, the
generated `*.yaml`, `*_decode_stats.json`, `*_counts.parquet`,
`*_decode_summary.json`, `*_decode_report.html`, `deduplicated.parquet`).

---

## 2. PREPROCESS subworkflow — `pipeline/subworkflows/preprocess.nf`

**Goal:** turn one or more raw FASTQ files (local or `gs://`) into a single
uncompressed `merged.fastq` ready for DELi.

Processes:

| Process       | Input                                | Output                        | Tool        |
|---------------|--------------------------------------|-------------------------------|-------------|
| `CONCAT`      | tuple(read_type, list of fastq[.gz]) | `R1.fastq[.gz]`/`R2.fastq[.gz]` | `cat`       |
| `FASTP_MERGE` | concatenated R1 + R2                 | `merged.fastq`, `fastp.html`, `fastp.json` | `fastp -m --correction` |
| `DECOMPRESS`  | concatenated single-end R1            | `merged.fastq`                | `gunzip`    |

Logic in `workflow PREPROCESS`:

- Reads `params.read_1` (and optional `params.read_2`) — list or comma-separated
  string of paths. `Channel.fromPath` preserves `gs://` URIs so Nextflow will
  stage them automatically.
- **Paired-end** (`read_2` set): `CONCAT` runs twice (R1, R2) → `FASTP_MERGE`
  merges paired reads into a single read with overlap correction.
- **Single-end**: `CONCAT` runs once on R1 → `DECOMPRESS` produces `merged.fastq`.
- Only `FASTP_MERGE` publishes (`${out_dir}/qc/fastp.html` + `fastp.json`); the
  fastq itself stays in the work dir and flows on to DELI.

**Emits:** `fastq` — a path channel pointing at `merged.fastq`.

---

## 3. DELI subworkflow — `pipeline/subworkflows/deli.nf`

This is the heart of the pipeline. It takes the merged FASTQ and turns it into a
table of compound counts plus run statistics and an HTML report. It does so by
chunking the FASTQ, fanning out to many parallel `deli decode run` jobs,
aggregating, then chunking again to count compounds in parallel.

`workflow DELI` takes two inputs:

- `fastq_files` — `Path` channel for the merged FASTQ (used by `splitFastq`).
- `fastq_uri` — the same path as a URI string (used to embed the path into the
  generated decode YAML).

### 3.1 Generated YAML — `GenerateDecodeYaml`

- **In:** the merged FASTQ path (only used so the YAML records the actual
  staged path).
- **Out (published to `out_dir`):**
  `${selection_id}_${target_id}_${date_ran}.yaml`
- **What it is:** the DELi "selection file". Contains selection metadata
  (`selection_id`, `target_id`, `selection_condition`, `date_ran`,
  `additional_info`), the list of `sequence_files`, the list of `libraries`
  to decode against, and the `decode_settings`
  (`library_error_tolerance`, `min_library_overlap`, `revcomp`,
  `demultiplexer_algorithm`, `demultiplexer_mode`, `realign`, `wiggle`).

This YAML is the canonical configuration consumed by every DELi step below —
it tells DELi which libraries to look up in `deli_data_dir` and which parsing
options to use.

### 3.2 `ExtractSequenceFiles`

Small Python helper that reads the generated YAML and writes:

- `selection_id.txt` — used to derive the prefix for output filenames
  (overridden by `params.prefix` if set).
- `files.txt` — list of sequence files declared in the YAML (informational; the
  actual FASTQ Path comes from the channel, not this file — see the inline
  comment in `deli.nf`).

### 3.3 `splitFastq` → `DecodeChunk` (parallel)

```
fastq_chunks = fastq_files.splitFastq(by: params.chunk_size, file: true)
```

Every `chunk_size` reads (default 1,000,000) become one chunk, each fed into a
parallel `DecodeChunk` job.

`DecodeChunk` runs:

```
deli ${deli_args} decode run \
    "${selection_file}" \
    "${fastq_chunk}" \
    --out-dir ./ \
    --prefix "${prefix}_${fastq_chunk.baseName}" \
    --skip-report
```

DELi CLI mapping (`src/deli/cli.py decode run`): converts DNA reads into DEL
compound identities by looking up library/building-block tags in
`deli_data_dir`.

- **In per chunk:** the chunk FASTQ + the selection YAML.
- **Out per chunk:**
  - `${prefix}_<chunk>_decoded.tsv` — per-read decode results (library id,
    building-block ids, UMI, score, etc.).
  - `${prefix}_<chunk>_decode_statistics.json` — per-chunk decode stats
    (counts of pass/fail, error breakdown, etc.).
  - `deli.log` — per-chunk DELi log.

`deli_args` is assembled in the workflow and may include `--debug`,
`--deli-data-dir`, and `--config-file` (validated against a safe-path regex
before being injected).

### 3.4 `CollectDecodeChunks` (gather)

```
deli ${deli_args} decode collect \
    *_decoded.tsv \
    --out-loc "${prefix}_collected.ndjson"
```

CLI mapping: `decode collect` — folds all per-read TSVs into NDJSON records,
one per `(library_id, bb_ids)` combination, with a list of UMI counts. This
representation is what enables the next chunked count step.

- **In:** all `*_decoded.tsv` from `DecodeChunk` (collected).
- **Out:** `${prefix}_collected.ndjson` (NOT published — internal).

### 3.5 `MergeDecodeStatistics`

```
deli ${deli_args} decode merge-stats \
    *_decode_statistics.json \
    --selection-file "${selection_file}" \
    --out-loc "${prefix}_decode_stats.json"
```

CLI mapping: `decode merge-stats` — consolidates all per-chunk stats JSONs into
one.

- **In:** every `*_decode_statistics.json` + the selection YAML.
- **Out (published):** `${prefix}_decode_stats.json`.

### 3.6 `WriteDecodeReport`

```
deli ${deli_args} decode report \
    "${final_stats}" \
    --selection-file "${selection_file}" \
    --out-loc "${prefix}_decode_report.html"
```

CLI mapping: `decode report` — renders the merged stats into an HTML report.

- **In:** `${prefix}_decode_stats.json` + selection YAML.
- **Out (published):** `${prefix}_decode_report.html`.

### 3.7 `splitText` → `CountChunk` (parallel)

```
count_chunks = collected_decodes.ndjson.splitText(by: 500_000, file: true)
```

Each chunk = 500,000 NDJSON lines. Parallel `CountChunk` runs:

```
deli ${deli_args} decode count \
    "${ndjson_chunk}" \
    --out-loc "${chunk}_counted.parquet" \
    --output-format parquet \
    --cluster-umis \
    --keep-raw-count \
    --keep-dedup-count
```

CLI mapping: `decode count` — turns NDJSON of decoded reads into a counts
table. With `--cluster-umis` it deduplicates UMIs to estimate molecule counts;
`--keep-raw-count`/`--keep-dedup-count` keep the intermediate raw and
deduplicated counts as additional columns alongside the clustered count.

- **In:** one NDJSON chunk.
- **Out:** `<chunk>_counted.parquet`.

### 3.8 `CollectCountChunks` (gather)

A small Polars script — not a DELi CLI — that lazily reads every chunk parquet
and sinks them into one parquet:

```
pl.scan_parquet(files).sink_parquet("${prefix}_counts.parquet")
```

- **In:** every `*_counted.parquet` from `CountChunk`.
- **Out (published):** `${prefix}_counts.parquet` — **the counts table that
  feeds POSTPROCESS** and is the typical input when re-running with
  `counts_file` set.

### 3.9 `SummarizeDecodeRun`

```
deli ${deli_args} decode summarize \
    "${merged_counts}" \
    "${decode_stats}" \
    --out-loc "${prefix}_decode_summary.json"
```

CLI mapping: `decode summarize` — combines the merged counts parquet with the
merged decode-stats JSON to produce a per-library summary
(total sequences, compounds, unique molecules).

- **In:** `${prefix}_counts.parquet` + `${prefix}_decode_stats.json`.
- **Out (published, mode `move`):** `${prefix}_decode_summary.json`.

### 3.10 DELI emits

```
counts  = ${prefix}_counts.parquet
summary = ${prefix}_decode_summary.json
report  = ${prefix}_decode_report.html
```

Only `counts` is wired into `POSTPROCESS`.

### DELI input/output cheat-sheet

| Input the subworkflow needs                | Comes from                               |
|--------------------------------------------|------------------------------------------|
| Merged FASTQ (path + URI)                  | `PREPROCESS.out.fastq` / `.toUriString()` |
| Selection metadata (selection_id, target_id, date_ran, etc.) | `params.yml`              |
| Library list                               | `params.libraries`                       |
| Decode tuning (error tolerance, revcomp…)  | `params.yml` decode-settings block       |
| DELi reference data (library JSONs, bb tables) | `params.deli_data_dir`               |
| Optional DELi config                       | `params.config_file`                     |

| Output the subworkflow produces            | Where                                    |
|--------------------------------------------|------------------------------------------|
| Generated `*.yaml` (the selection file)    | `${out_dir}/`                            |
| `${prefix}_decode_stats.json`              | `${out_dir}/`                            |
| `${prefix}_decode_report.html`             | `${out_dir}/`                            |
| `${prefix}_counts.parquet`                 | `${out_dir}/` (also fed to POSTPROCESS)  |
| `${prefix}_decode_summary.json`            | `${out_dir}/`                            |

---

## 4. POSTPROCESS subworkflow — `pipeline/subworkflows/postprocess.nf`

Linear two-step pipeline. Both steps run small Python scripts shipped inside
this repo (`/opt/deliver/src/deliver/postprocess/...` in the container).

| Process       | Command                                                                    | In                       | Out (published)         |
|---------------|----------------------------------------------------------------------------|--------------------------|-------------------------|
| `DEDUPLICATE` | `python /opt/deliver/src/deliver/postprocess/deduplicate.py --input … --output deduplicated.parquet` | counts parquet           | `deduplicated.parquet`  |
| `ENRICHMENT`  | `python /opt/deliver/src/deliver/postprocess/enrichment.py --input … --output enrichment.parquet` | `deduplicated.parquet`   | `enrichment.parquet`    |

> **Note (current state of the repo):** both `deduplicate.py` and
> `enrichment.py` are scaffolded but logic is `# TODO` — they currently just
> read the parquet and write it back unchanged. So today the published
> `enrichment.parquet` is byte-equivalent to the input counts; the subworkflow
> wiring is in place ahead of the algorithmic work.

`POSTPROCESS` emits `results = ENRICHMENT.out.enrichment`.

---

## 5. The final file

Across the full path (`PREPROCESS → DELI → POSTPROCESS`):

- **Final file:** `${out_dir}/enrichment.parquet`
- Produced by: `POSTPROCESS.ENRICHMENT` (`enrichment.py`).
- Schema (today): same as `${prefix}_counts.parquet` (see §3.7 — at minimum
  `library_id`, `bb_ids`, clustered count, plus `--keep-raw-count` and
  `--keep-dedup-count` columns from `deli decode count`). Once the TODOs in
  the postprocess scripts are filled in, this file will additionally carry
  per-compound enrichment scores.

In the alternative `counts_file` mode, the input is already a counts parquet, so
`PREPROCESS` and `DELI` are skipped and `enrichment.parquet` is still the
final artifact.

Other useful artifacts published to `${out_dir}` along the way:

- `qc/fastp.html`, `qc/fastp.json` — preprocessing QC.
- `${selection_id}_${target_id}_${date_ran}.yaml` — generated DELi selection
  file (run reproducibility).
- `${prefix}_decode_stats.json` — merged decode statistics.
- `${prefix}_decode_report.html` — human-readable decode report.
- `${prefix}_counts.parquet` — pre-postprocess counts table.
- `${prefix}_decode_summary.json` — per-library decode summary.
- `deduplicated.parquet` — intermediate post-decode artifact.
- `enrichment.parquet` — **final output**.

---

## 6. DELi CLI commands invoked, at a glance

Mapped against `src/deli/cli.py` on branch `patch`:

| Pipeline process        | DELi CLI                | Purpose                                              |
|-------------------------|-------------------------|------------------------------------------------------|
| `DecodeChunk`           | `deli decode run`       | DNA reads → per-read decoded TSV + stats JSON        |
| `CollectDecodeChunks`   | `deli decode collect`   | Decoded TSVs → NDJSON aggregated by (lib, bb_ids)    |
| `MergeDecodeStatistics` | `deli decode merge-stats` | Merge per-chunk stats JSONs                        |
| `WriteDecodeReport`     | `deli decode report`    | Stats JSON → HTML report                             |
| `CountChunk`            | `deli decode count`     | NDJSON chunk → counts parquet (UMI-clustered)        |
| `CollectCountChunks`    | (Polars, not DELi)      | Concatenate counts parquets via `pl.scan_parquet`    |
| `SummarizeDecodeRun`    | `deli decode summarize` | Counts + stats → per-library summary JSON            |

The DELI subworkflow therefore consumes:

- one merged FASTQ (the only "data" input),
- a selection YAML describing libraries and decode settings,
- the DELi data dir (library/building-block reference data),

and produces a counts parquet (the load-bearing output), plus a stats JSON,
summary JSON, and HTML report as observability artifacts.
