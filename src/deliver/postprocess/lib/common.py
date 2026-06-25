"""Shared utilities for postprocessing scripts."""

import json
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import COMPOUND_ID, CORRECTED_COUNT, LIBRARY_ID, RAW_COUNT

COMMON_FORMAT_COLUMNS = {COMPOUND_ID, LIBRARY_ID, RAW_COUNT, CORRECTED_COUNT}


def validate_common_format(df: pl.DataFrame) -> None:
    """Validate that a dataframe conforms to the common postprocessing format."""
    missing = COMMON_FORMAT_COLUMNS - set(df.columns)
    if missing:
        print(f"Error: missing required columns: {missing}", file=sys.stderr)
        sys.exit(1)

    dupes = df.filter(pl.col(COMPOUND_ID).is_duplicated())
    if len(dupes) > 0:
        print(f"Error: compound_id is not unique ({len(dupes)} duplicate rows)", file=sys.stderr)
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
