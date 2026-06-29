# Contributing — postprocessing

Quick reference for the three most common changes to the postprocessing pipeline.

---

## 1. Deduplication logic

**File:** `src/deliver/postprocess/deduplicate.py` — `deduplicate()`

When `on_duplicate_compound_id = "sum"`, duplicate rows are merged with:
- `corrected_count`, `raw_reads` — **summed**
- `z_score` (pre-supplied) — combined with **Stouffer's method**: `sum(z) / sqrt(n)`
- all other columns — **first value kept**

To change how any column is aggregated, edit the `group_by().agg(...)` block. To add a new column that needs special aggregation (e.g. weighted mean), add it to that agg list and exclude it from `first_cols`.

Tests: `tests/test_postprocess.py` — `test_sum_mode_*`

---

## 2. Adding or changing enrichment metrics

Metrics are shared between singleton and disynthon calculations.

**Step 1 — add the formula** to `src/deliver/postprocess/lib/metrics.py`.
Follow the pattern of `z_score()` (function on a Polars Series) or `PolyO` (class for stateful calculation).

**Step 2 — add the output column name** to `src/deliver/postprocess/lib/columns.py`.

**Step 3 — compute for singletons** in `src/deliver/postprocess/singleton.py`, inside `enrichment()`.
Add a `with_columns()` expression per library, similar to how `Z_SCORE_LIB`, `Z_SCORE_GLOBAL`, and `POLYO` are computed.

**Step 4 — compute for disynthons** in `src/deliver/postprocess/disynthons.py`, inside `_add_lib_statistics()`.
Disynthons aggregate at the group level, so add the formula using the aggregated `corrected_count`.

**Step 5 — expose in labeling** (see section 3 below) if the metric will be used for hit calling.

---

## 3. Adding a new label

**File:** `src/deliver/postprocess/label.py`

**Step 1 — write the function.** It receives the full enriched DataFrame and returns a Polars boolean expression:

```python
def label_my_metric(df: pl.DataFrame) -> pl.Expr:
    return (
        (pl.col(CORRECTED_COUNT) > _COUNT_THRESHOLD)
        & (pl.col(MY_METRIC) > _MY_THRESHOLD)
    )
```

Thresholds are module-level constants at the top of the file.

**Step 2 — register it** in the two dicts:

```python
MODES = {
    ...
    "count_my_metric": label_my_metric,
}

_MODE_REQUIRED_COLS = {
    ...
    "count_my_metric": [CORRECTED_COUNT, MY_METRIC],
}
```

The mode name becomes the output column name: `label_count_my_metric`.

**Step 3 — use it** in a params file:

```yaml
labeling:
  - count_my_metric
```

Tests: `tests/test_postprocess.py` — add `test_count_my_metric_*` following existing label test patterns, and add the mode to `test_all_modes_available`.
