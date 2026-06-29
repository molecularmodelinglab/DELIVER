"""Deduplicate DEL counts by compound ID."""

import argparse
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import COMPOUND_ID, CORRECTED_COUNT, RAW_READS

_SMILES = "SMILES"


def _fmt(ids: list) -> str:
    return f"{ids[:5]}{'...' if len(ids) > 5 else ''}"


def deduplicate(df: pl.DataFrame, on_duplicate_compound_id: str) -> pl.DataFrame:
    if not df[COMPOUND_ID].is_duplicated().any():
        return df

    if _SMILES in df.columns:
        # SMILES must agree across all rows sharing a compound_id before we can merge or fail.
        conflicts = (
            df.group_by(COMPOUND_ID)
            .agg(pl.col(_SMILES).n_unique().alias("n_smiles"))
            .filter(pl.col("n_smiles") > 1)
        )
        if len(conflicts) > 0:
            ids = conflicts[COMPOUND_ID].to_list()
            raise ValueError(
                f"{len(ids)} compound ID(s) have conflicting SMILES across duplicate rows: {_fmt(ids)}"
            )

    if on_duplicate_compound_id == "fail":
        dup_ids = df.filter(pl.col(COMPOUND_ID).is_duplicated())[COMPOUND_ID].unique().to_list()
        raise ValueError(
            f"{len(dup_ids)} duplicate compound ID(s) found: {_fmt(dup_ids)}"
        )

    # sum mode: merge duplicate rows by summing counts, keeping first value for all other columns
    sum_cols   = [c for c in [CORRECTED_COUNT, RAW_READS] if c in df.columns]
    first_cols = [c for c in df.columns if c != COMPOUND_ID and c not in sum_cols]
    result = df.group_by(COMPOUND_ID).agg(
        [pl.col(c).sum()   for c in sum_cols] +
        [pl.col(c).first() for c in first_cols]
    )
    return result.select(df.columns)  # restore original column order


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",  required=True, help="Input normalized parquet file.")
    parser.add_argument("--output", required=True, help="Output deduplicated parquet file.")
    parser.add_argument(
        "--on-duplicate-compound-id",
        required=True,
        choices=["fail", "sum"],
        help=(
            "What to do when the same compound_id appears more than once. "
            "'fail' aborts with an error listing the offending IDs (use to catch unexpected duplicates). "
            "'sum' merges duplicate rows by summing corrected_count and raw_count. "
            "If a SMILES column is present, all duplicate rows must have the same SMILES — fails otherwise."
        ),
    )
    parsed = parser.parse_args(args)

    input_path = Path(parsed.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    df = pl.read_parquet(input_path)
    try:
        deduplicate(df, parsed.on_duplicate_compound_id).write_parquet(parsed.output)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
