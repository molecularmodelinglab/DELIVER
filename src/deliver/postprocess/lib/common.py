"""Shared utilities for postprocessing scripts."""

import json
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import COMPOUND_ID, CORRECTED_COUNT, LIBRARY_ID

COMMON_FORMAT_COLUMNS = {COMPOUND_ID, LIBRARY_ID, CORRECTED_COUNT}


def validate_common_format(df: pl.DataFrame) -> None:
    """Validate that a dataframe conforms to the common postprocessing format."""
    missing = COMMON_FORMAT_COLUMNS - set(df.columns)
    if missing:
        print(f"Error: missing required columns: {missing}", file=sys.stderr)
        sys.exit(1)

    # Null bytes in compound_id are a signature of a corrupted string buffer
    # (e.g. an uninitialized allocation that never got written), not a real
    # value — surface it here, at the point compound_id is first validated,
    # instead of downstream where it just looks like an unexplained duplicate.
    corrupt = df.filter(pl.col(COMPOUND_ID).str.contains("\x00", literal=True))
    if corrupt.height > 0:
        ids = corrupt[COMPOUND_ID].unique().to_list()
        print(
            f"Error: {len(ids)} compound_id value(s) contain null bytes "
            f"(corrupted data, not a real ID): {ids[:5]}",
            file=sys.stderr,
        )
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
