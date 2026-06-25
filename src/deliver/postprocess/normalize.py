"""Normalize DELi counts parquet to common format."""

import argparse
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import COMPOUND_ID, CORRECTED_COUNT, LIBRARY_ID, RAW_COUNT
from deliver.postprocess.lib.common import validate_common_format

DELI_REQUIRED_COLUMNS = {"library_id", "bb_ids", "count", "raw_count"}


def _max_cycles(bb_ids: pl.Series) -> int:
    return bb_ids.str.split(",").list.len().max()


def _add_cycle_columns(df: pl.DataFrame, n_cycles: int) -> pl.DataFrame:
    """Split bb_ids into compound_id and one column per cycle (A, B, C...).

    Rows with fewer cycles than n_cycles get null for the missing columns.
    """
    cycle_letters = [chr(ord("A") + i) for i in range(n_cycles)]
    compound_id = (pl.col(LIBRARY_ID) + "-" + pl.col("bb_ids").str.replace_all(",", "-")).alias(COMPOUND_ID)

    return (
        df
        .with_columns(compound_id, pl.col("bb_ids").str.split(",").alias("_bb_list"))
        .with_columns([
            # get the i-th building block id and put it in the corresponding cycle column
            pl.col("_bb_list").list.get(i, null_on_oob=True).alias(letter)
            for i, letter in enumerate(cycle_letters)
        ])
        .drop("_bb_list")
    )


def normalize(df: pl.DataFrame) -> pl.DataFrame:
    missing = DELI_REQUIRED_COLUMNS - set(df.columns)
    if missing:
        print(f"Error: missing required columns in DELi counts: {missing}", file=sys.stderr)
        sys.exit(1)

    n_cycles = _max_cycles(df["bb_ids"])
    cycle_cols = [chr(ord("A") + i) for i in range(n_cycles)]

    return (
        _add_cycle_columns(df, n_cycles)
        .rename({"count": CORRECTED_COUNT})
        .select([COMPOUND_ID, LIBRARY_ID] + cycle_cols + [RAW_COUNT, CORRECTED_COUNT])
    )


def main(args=None):
    parser = argparse.ArgumentParser(description="Normalize DELi counts parquet to common format.")
    parser.add_argument("--input",  required=True, help="Input DELi counts parquet file.")
    parser.add_argument("--output", required=True, help="Output normalized parquet file.")
    parsed = parser.parse_args(args)

    input_path = Path(parsed.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    df = pl.read_parquet(input_path)
    n_phantom = df.filter(pl.col("bb_ids") == "").height
    if n_phantom > 0:
        print(f"Warning: dropping {n_phantom} rows", file=sys.stderr)
        df = df.filter(pl.col("bb_ids") != "")
    df = normalize(df)
    validate_common_format(df)
    df.write_parquet(parsed.output)


if __name__ == "__main__":
    main()
