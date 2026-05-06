"""Shared utilities for postprocessing scripts."""

import sys

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
