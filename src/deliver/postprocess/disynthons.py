"""Compute disynthon counts from normalized DEL counts."""

import argparse
import json
import math
import sys
from itertools import combinations
from pathlib import Path

import polars as pl

from deliver.postprocess.common import validate_common_format


def _cycle_cols(library_dict: dict) -> list[str]:
    """Sorted cycle column names present in the library dict (e.g. ['A', 'B', 'C'])."""
    return sorted({k for lib in library_dict.values() for k in lib})


def disynthon_counts(df: pl.DataFrame, col1: str, col2: str, library_dict: dict) -> pl.DataFrame:
    """Aggregate corrected_count and raw_count to disynthon level.

    Adds tot_compounds (including unobserved), mean_count, and std_count
    where mean and std treat unobserved compounds as zero.
    """
    agg = (
        df
        .filter(pl.col(col1).is_not_null() & pl.col(col2).is_not_null())
        .group_by(["library_id", col1, col2])
        .agg(
            pl.col("corrected_count").sum(),
            pl.col("raw_count").sum(),
            (pl.col("corrected_count") ** 2).sum().alias("_sum_sq"),
        )
        .sort(["library_id", col1, col2])
    )

    # tot_compounds = product of all OTHER cycle counts for this (col1, col2) pair
    lib_tot = {
        lib_id: math.prod(count for k, count in lib.items() if k not in {col1, col2})
        for lib_id, lib in library_dict.items()
        if col1 in lib and col2 in lib
    }
    lib_tot_df = pl.DataFrame({
        "library_id":    list(lib_tot.keys()),
        "tot_compounds": list(lib_tot.values()),
    })

    return (
        agg
        .join(lib_tot_df, on="library_id", how="left")
        .with_columns(
            (pl.col("corrected_count") / pl.col("tot_compounds")).alias("mean_count"),
        )
        .with_columns(
            (
                (pl.col("_sum_sq") / pl.col("tot_compounds") - pl.col("mean_count") ** 2).sqrt()
            ).alias("std_count"),
        )
        .drop("_sum_sq")
    )


def main(args=None):
    """Compute disynthon counts from normalized DEL counts."""
    parser = argparse.ArgumentParser(description="Compute disynthon counts from normalized DEL counts.")
    parser.add_argument("--input",        required=True, help="Input normalized parquet file.")
    parser.add_argument("--library-dict", required=True, help="Library dictionary JSON file.")
    parser.add_argument("--output-dir",   required=True, help="Output directory for disynthon parquet files.")
    parsed = parser.parse_args(args)

    input_path = Path(parsed.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    library_dict_path = Path(parsed.library_dict)
    if not library_dict_path.exists():
        print(f"Error: library dict not found: {library_dict_path}", file=sys.stderr)
        sys.exit(1)

    df = pl.read_parquet(input_path)
    validate_common_format(df)

    library_dict = json.loads(library_dict_path.read_text())

    output_dir = Path(parsed.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for col1, col2 in combinations(_cycle_cols(library_dict), 2):
        name = col1 + col2
        disynthon_counts(df, col1, col2, library_dict).write_parquet(output_dir / f"disynthons_{name}.parquet")


if __name__ == "__main__":
    main()
