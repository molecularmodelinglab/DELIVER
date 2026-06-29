# DELIVER — Output File Reference

The two primary analysis outputs are `enriched.parquet` and (optionally) `labeled.parquet`.
Both are standard Parquet files readable with Polars, Pandas, or any Parquet-compatible tool.

---

## enriched.parquet

One row per compound. Contains compound identity, raw counts, enrichment metrics at the
singleton level, and enrichment metrics at the disynthon level for every cycle pair.

### Compound identity

| Column       | Type   | Description                                      |
|--------------|--------|--------------------------------------------------|
| `compound_id` | String | `library_id-bbA-bbB-bbC` — unique compound key  |
| `library_id`  | String | Library this compound belongs to                 |
| `A`, `B`, `C` | String | Building block IDs for each cycle                |
| `SMILES`      | String | SMILES string (present only if SMILES were joined or supplied) |

### Counts

| Column            | Type | Description                                           |
|-------------------|------|-------------------------------------------------------|
| `corrected_count` | Int  | UMI-corrected count — primary enrichment metric       |
| `raw_reads`       | Int  | Raw read count (present only if supplied in input)    |

### Singleton enrichment metrics

| Column                     | Type  | Description                                                        |
|----------------------------|-------|--------------------------------------------------------------------|
| `z_score`                  | Float | Pre-supplied z-score carried through from input (not recalculated) |
| `z_score_lib_normalized`   | Float | Binomial z-score relative to this library's compound space         |
| `z_score_global_normalized`| Float | Binomial z-score relative to all libraries combined                |
| `polyo`                    | Float | PolyO enrichment score (Poisson)                                   |

`z_score` is only present if supplied in the input. `z_score_lib_normalized` and `z_score_global_normalized`
are computed only when `z_score` is **not** present — if a pre-supplied z-score is
carried through, the pipeline skips recalculation and neither column is added.

### Disynthon enrichment metrics

One set of columns per cycle pair (AB, BC, AC, …). Replace `ab_` with `bc_`, `ac_`, etc.

| Column                      | Type  | Description                                                            |
|-----------------------------|-------|------------------------------------------------------------------------|
| `ab_corrected_count_sum`    | Int   | Sum of corrected counts for all compounds in this disynthon            |
| `ab_line_size`              | Int   | Number of singleton compounds in this disynthon (product of remaining cycles) |
| `ab_line_strength`          | Float | Mean corrected count per compound in this disynthon                    |
| `ab_line_strength_std`      | Float | Standard deviation of corrected count within this disynthon            |
| `ab_z_score_lib_normalized` | Float | Binomial z-score of this disynthon relative to library disynthon space |
| `ab_z_score_global_normalized` | Float | Binomial z-score of this disynthon relative to all libraries combined  |
| `ab_polyo`                  | Float | PolyO score for this disynthon                                         |
| `ab_raw_reads_sum`          | Int   | Sum of raw reads (present only if `raw_reads` was in input)            |

---

## labeled.parquet

All columns from `enriched.parquet`, plus one boolean column per labeling mode that
was enabled in `params.labeling`.

| Column                   | Type | Positive when |
|--------------------------|------|---------------|
| `label_count`            | Bool | `corrected_count > 5` |
| `label_count_zscore_lib` | Bool | `corrected_count > 5` AND (`z_score_lib_normalized > 1` OR any `*_z_score_lib_normalized > 1`) |
| `label_count_zscore_global` | Bool | `corrected_count > 5` AND (`z_score_global_normalized > 1` OR any `*_z_score_global_normalized > 1`) |
| `label_count_polyo`      | Bool | `corrected_count > 5` AND (`polyo > 4` OR any `*_polyo > 4`) |

Only the modes listed in `params.labeling` are added; others are absent.

---

## SMILES duplicate files

When a `SMILES` column is present and the same SMILES string maps to more than one
`compound_id` (same chemical structure reachable via different building block
combinations), the duplicate rows are extracted and saved as a separate file:

- `enriched_duplicates.parquet` — same schema as `enriched.parquet`
- `labeled_duplicates.parquet` — same schema as `labeled.parquet`

Both are sorted by SMILES so duplicate groups appear together. These files are only
written when duplicates exist; they are absent otherwise.

---

## Important notes

**PolyO uses corrected (UMI-deduplicated) counts throughout.**
Both `d` (expected reads per compound, used to set the Poisson threshold) and `s_bar`
(mean reads per compound in the library) are computed from `corrected_count`. This
follows the paper's definition of "total number of decodable reads" as the number of
unique decoded sequences. Raw counts are not required and do not affect PolyO.

**Disynthon PolyO includes only observed compounds.**
Per the original publication, the disynthon PolyO probability is computed as the
product over compounds with `ki > 0` only. Unobserved compounds in the disynthon are
excluded from the product. This means disynthon PolyO reflects enrichment of the
actually-observed building block combination, not the theoretical maximum.

**Two z-score levels are always computed.**
`z_score_lib` is the binomial z-score relative to the number of possible compounds in
the same library. `z_score_global` is relative to all compounds across all libraries
combined. For multi-library experiments, `z_score_global` is harder to exceed and acts
as a stricter filter.

**Labeling modes check disynthon columns dynamically.**
Modes that involve disynthon metrics (e.g. `count_zscore_lib`) look for any column
ending in `_z_score_lib_normalized` (i.e. `ab_z_score_lib_normalized`, `bc_z_score_lib_normalized`, etc.). If no
disynthon columns are present, the condition falls back to the singleton metric alone.
This means labeling still works if run on `singletons.parquet` instead of
`enriched.parquet`, though disynthon-level evidence will not be considered.

**Column presence depends on input.**
`raw_reads`, `SMILES`, and `z_score` (pre-supplied) are only present if they were in
the original input or joined during the pipeline. Code that reads these files should
check for column presence before using them.
