"""Shared utilities for postprocessing scripts."""

import json
import os
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import COMPOUND_ID, CORRECTED_COUNT, LIBRARY_ID

COMMON_FORMAT_COLUMNS = {COMPOUND_ID, LIBRARY_ID, CORRECTED_COUNT}


_CONTROL_CHARS = r"[\x00-\x1f\x7f]"


def _cycle_columns(df: pl.DataFrame | pl.LazyFrame) -> list[str]:
    """The building-block columns A, B, C, ... that normalize writes, in order."""
    columns = column_names(df)
    cols = []
    for i in range(26):
        name = chr(ord("A") + i)
        if name not in columns:
            break
        cols.append(name)
    return cols


def column_names(df: pl.DataFrame | pl.LazyFrame) -> list[str]:
    """Column names of an eager or lazy frame (a LazyFrame's without reading any data)."""
    return df.collect_schema().names() if isinstance(df, pl.LazyFrame) else df.columns


def _bad_compound_id_expr(df: pl.DataFrame | pl.LazyFrame, check_building_blocks: bool) -> pl.Expr:
    bad = pl.col(COMPOUND_ID).is_null() | pl.col(COMPOUND_ID).str.contains(_CONTROL_CHARS)
    cycles = _cycle_columns(df)
    if check_building_blocks and LIBRARY_ID in column_names(df) and cycles:
        # null when a cycle is null (a library with fewer cycles): not checked
        expected = pl.concat_str([pl.col(LIBRARY_ID), *[pl.col(c) for c in cycles]], separator="-")
        bad = bad | (expected.is_not_null() & (pl.col(COMPOUND_ID) != expected))
    return bad


def compound_id_problems(
    df: pl.DataFrame | pl.LazyFrame, check_building_blocks: bool = False
) -> tuple[int, list[str]]:
    """Count compound IDs that cannot be right, with a few examples.

    A compound ID is wrong if it is null or contains control characters: a
    block of IDs zeroed to NUL bytes is how a corrupted string buffer shows up
    (seen twice, in SGC-DEL0010 rows, when single-CPU tasks ran ~16 polars
    threads).

    With ``check_building_blocks`` it must also equal ``library_id-A-B-C``.
    That holds for IDs normalize builds from DELi counts; external counts
    formats may split IDs differently, so it is not checked for them.
    """
    if COMPOUND_ID not in column_names(df):
        return 0, []
    bad = _bad_compound_id_expr(df, check_building_blocks)
    if isinstance(df, pl.LazyFrame):
        # streamed: only the offending IDs are ever held in memory
        rows = df.filter(bad).select(COMPOUND_ID).collect(engine="streaming")
    else:
        rows = df.filter(bad)
    return rows.height, [ascii(x) for x in rows[COMPOUND_ID].head(5).to_list()]


def validate_compound_ids(df: pl.DataFrame | pl.LazyFrame, step: str, check_building_blocks: bool = False) -> None:
    """Raise ValueError if any compound ID is corrupted (see compound_id_problems)."""
    n, examples = compound_id_problems(df, check_building_blocks)
    if n:
        raise ValueError(
            f"{step}: {n} corrupted compound_id value(s), e.g. {', '.join(examples)}. "
            "The data was damaged after the IDs were built, so this is not a real "
            "duplicate or decode problem. Rerun postprocessing from the counts parquet "
            "in a fresh work directory (do not -resume)."
        )


def validate_common_format(df: pl.DataFrame | pl.LazyFrame) -> None:
    """Validate that a dataframe conforms to the common postprocessing format."""
    missing = COMMON_FORMAT_COLUMNS - set(column_names(df))
    if missing:
        print(f"Error: missing required columns: {missing}", file=sys.stderr)
        sys.exit(1)

    # Null bytes in compound_id are a signature of a corrupted string buffer
    # (e.g. an uninitialized allocation that never got written), not a real
    # value — surface it here, at the point compound_id is first validated,
    # instead of downstream where it just looks like an unexplained duplicate.
    try:
        validate_compound_ids(df, "validate_common_format")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def load_inputs(input_path: Path, library_dict_path: Path) -> tuple[pl.DataFrame, dict]:
    """Load and validate input parquet + library dict. Exits on error."""
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    if not library_dict_path.exists():
        print(f"Error: library dict not found: {library_dict_path}", file=sys.stderr)
        sys.exit(1)
    df = pl.read_parquet(input_path)
    validate_common_format(df)
    return df, json.loads(library_dict_path.read_text())


def scan_inputs(input_path: Path, library_dict_path: Path) -> tuple[pl.LazyFrame, dict]:
    """load_inputs, but returns a LazyFrame: validated by streaming, never loaded whole.

    For runs too large to hold in memory (328M compounds for TREX1); the
    validation and its error messages are the same as load_inputs.
    """
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    if not library_dict_path.exists():
        print(f"Error: library dict not found: {library_dict_path}", file=sys.stderr)
        sys.exit(1)
    lf = pl.scan_parquet(input_path)
    validate_common_format(lf)
    return lf, json.loads(library_dict_path.read_text())


def duplicated_values(lf: pl.LazyFrame, col: str) -> pl.Series:
    """Non-null values of `col` that occur in more than one row, found by streaming.

    Same result as ``df[col].filter(df[col].is_duplicated()).unique()``, without
    holding the column in memory: rows are grouped by a 64-bit hash of the value
    (8 bytes a row), and only the rows whose hash repeats are compared exactly.
    Equal values always share a hash, so nothing is missed; a hash collision
    between different values only adds a candidate that the exact check drops.
    """
    value = pl.col(col)
    candidates = (
        lf.filter(value.is_not_null())
        .group_by(value.hash().alias("_hash"))
        .agg(pl.len().alias("_n"))
        .filter(pl.col("_n") > 1)
        .select("_hash")
        .collect(engine="streaming")["_hash"]
    )
    if candidates.is_empty():
        return pl.Series(col, [], dtype=lf.collect_schema()[col])
    rows = (
        lf.filter(value.is_not_null() & value.hash().is_in(candidates.implode()))
        .select(col)
        .collect(engine="streaming")
    )
    return rows.filter(value.is_duplicated())[col].unique()


def has_duplicate_ids(lf: pl.LazyFrame) -> bool:
    """Whether any compound_id occurs more than once (see duplicated_values)."""
    return not duplicated_values(lf, COMPOUND_ID).is_empty()


def sink_parquet(lf: pl.LazyFrame, output: str | Path) -> None:
    """Stream lf to a parquet file, replacing it only once fully written.

    Writes to a temporary file beside `output` and renames it into place, so a
    query that reads `output` itself (the same path as input and output) never
    sees it truncated, and a failure never leaves a partial file behind.
    """
    output = Path(output)
    tmp = output.with_name(f".{output.name}.tmp")
    try:
        lf.sink_parquet(tmp)
        os.replace(tmp, output)
    finally:
        tmp.unlink(missing_ok=True)
