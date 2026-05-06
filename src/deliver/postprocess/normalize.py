"""Normalize DELi counts parquet to common format."""

import argparse
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.common import validate_common_format

DELI_REQUIRED_COLUMNS = {"library_id", "bb_ids", "count", "raw_count"}


def normalize(df: pl.DataFrame) -> pl.DataFrame:
    missing = DELI_REQUIRED_COLUMNS - set(df.columns)
    if missing:
        print(f"Error: missing required columns in DELi counts: {missing}", file=sys.stderr)
        sys.exit(1)

    bb_lists = df["bb_ids"].str.split(",")
    max_cycles = bb_lists.list.len().max()

    df = df.with_columns(
        (pl.col("library_id") + "-" + pl.col("bb_ids").str.replace_all(",", "-")).alias("compound_id"),
        bb_lists.alias("_bb_list"),
    )

    for i in range(max_cycles):
        letter = chr(ord("A") + i)
        df = df.with_columns(pl.col("_bb_list").list.get(i).alias(letter))

    cols_to_drop = ["_bb_list", "bb_ids"] + [c for c in ["dedup_count"] if c in df.columns]
    cycle_cols = [chr(ord("A") + i) for i in range(max_cycles)]

    return (
        df
        .drop(cols_to_drop)
        .rename({"count": "corrected_count"})
        .select(["compound_id", "library_id"] + cycle_cols + ["raw_count", "corrected_count"])
    )


def main(args=None):
    """Normalize DELi counts parquet to common format."""
    parser = argparse.ArgumentParser(description="Normalize DELi counts parquet to common format.")
    parser.add_argument("--input",  required=True, help="Input DELi counts parquet file.")
    parser.add_argument("--output", required=True, help="Output normalized parquet file.")
    parsed = parser.parse_args(args)

    input_path = Path(parsed.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    df = pl.read_parquet(input_path)
    df = normalize(df)
    validate_common_format(df)
    df.write_parquet(parsed.output)


if __name__ == "__main__":
    main()
