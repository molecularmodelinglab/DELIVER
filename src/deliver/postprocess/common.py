"""Shared utilities for postprocessing scripts."""

import json
import math
import sys
from pathlib import Path

import polars as pl

COMMON_FORMAT_COLUMNS = {"compound_id", "library_id", "raw_count", "corrected_count"}


def validate_common_format(df: pl.DataFrame) -> None:
    """Validate that a dataframe conforms to the common postprocessing format."""
    missing = COMMON_FORMAT_COLUMNS - set(df.columns)
    if missing:
        print(f"Error: missing required columns: {missing}", file=sys.stderr)
        sys.exit(1)

    dupes = df.filter(pl.col("compound_id").is_duplicated())
    if len(dupes) > 0:
        print(f"Error: compound_id is not unique ({len(dupes)} duplicate rows)", file=sys.stderr)
        sys.exit(1)


def add_z_score_norm(df: pl.DataFrame, n_compounds: int) -> pl.DataFrame:
    """Add z_score_norm column using the per-library binomial enrichment formula.

    Returns NaN when z-score is undefined (n_compounds == 1).
    """
    n_total = df["corrected_count"].sum()
    c_expected = n_total / n_compounds
    denom = math.sqrt(c_expected * (1 - c_expected / n_total))
    if denom == 0:
        return df.with_columns(pl.lit(float("nan")).alias("z_score_norm"))
    return df.with_columns(
        ((pl.col("corrected_count") - c_expected) / denom / math.sqrt(n_total)).alias("z_score_norm")
    )


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
