"""Shared utilities for postprocessing scripts."""

import json
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import COMPOUND_ID, CORRECTED_COUNT, LIBRARY_ID

COMMON_FORMAT_COLUMNS = {COMPOUND_ID, LIBRARY_ID, CORRECTED_COUNT}


_CONTROL_CHARS = r"[\x00-\x1f\x7f]"


def _cycle_columns(df: pl.DataFrame) -> list[str]:
    """The building-block columns A, B, C, ... that normalize writes, in order."""
    cols = []
    for i in range(26):
        name = chr(ord("A") + i)
        if name not in df.columns:
            break
        cols.append(name)
    return cols


def compound_id_problems(
    df: pl.DataFrame, check_building_blocks: bool = False
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
    if COMPOUND_ID not in df.columns:
        return 0, []
    bad = pl.col(COMPOUND_ID).is_null() | pl.col(COMPOUND_ID).str.contains(_CONTROL_CHARS)
    cycles = _cycle_columns(df)
    if check_building_blocks and LIBRARY_ID in df.columns and cycles:
        # null when a cycle is null (a library with fewer cycles): not checked
        expected = pl.concat_str([pl.col(LIBRARY_ID), *[pl.col(c) for c in cycles]], separator="-")
        bad = bad | (expected.is_not_null() & (pl.col(COMPOUND_ID) != expected))
    rows = df.filter(bad)
    return rows.height, [ascii(x) for x in rows[COMPOUND_ID].head(5).to_list()]


def validate_compound_ids(df: pl.DataFrame, step: str, check_building_blocks: bool = False) -> None:
    """Raise ValueError if any compound ID is corrupted (see compound_id_problems)."""
    n, examples = compound_id_problems(df, check_building_blocks)
    if n:
        raise ValueError(
            f"{step}: {n} corrupted compound_id value(s), e.g. {', '.join(examples)}. "
            "The data was damaged after the IDs were built, so this is not a real "
            "duplicate or decode problem. Rerun postprocessing from the counts parquet "
            "in a fresh work directory (do not -resume)."
        )


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
