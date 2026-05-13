"""Compute disynthon counts from normalized DEL counts."""

import argparse
import math
from itertools import combinations
from pathlib import Path

import polars as pl

from deliver.postprocess.common import add_z_score_norm, load_inputs


def _cycle_cols(library_dict: dict) -> list[str]:
    """Sorted cycle column names present in the library dict (e.g. ['A', 'B', 'C'])."""
    return sorted({k for lib in library_dict.values() for k in lib})


def disynthon_counts(df: pl.DataFrame, col1: str, col2: str, library_dict: dict) -> pl.DataFrame:
    """Aggregate to disynthon level per library.

    Columns added:
      tot_compounds  — product of remaining cycle counts (e.g. C for AB)
      mean_count     — corrected_count / tot_compounds (zeros included)
      std_count      — std of corrected_count across tot_compounds (zeros included)
      z_score_norm   — per-library binomial z-score (space = col1_count * col2_count)
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

    results = []
    for lib_id, lib in library_dict.items():
        if col1 not in lib or col2 not in lib:
            continue
        df_lib = agg.filter(pl.col("library_id") == lib_id)
        if len(df_lib) == 0:
            continue

        tot_compounds = math.prod(count for k, count in lib.items() if k not in {col1, col2})
        n_disynthons = lib[col1] * lib[col2]

        results.append(
            df_lib
            .with_columns(
                pl.lit(tot_compounds).alias("tot_compounds"),
                (pl.col("corrected_count") / tot_compounds).alias("mean_count"),
            )
            .with_columns(
                ((pl.col("_sum_sq") / tot_compounds - pl.col("mean_count") ** 2).sqrt()).alias("std_count"),
            )
            .drop("_sum_sq")
            .pipe(add_z_score_norm, n_disynthons)
        )

    return pl.concat(results)


def main(args=None):
    parser = argparse.ArgumentParser(description="Compute disynthon counts from normalized DEL counts.")
    parser.add_argument("--input",        required=True, help="Input normalized parquet file.")
    parser.add_argument("--library-dict", required=True, help="Library dictionary JSON file.")
    parser.add_argument("--output-dir",   required=True, help="Output directory for disynthon parquet files.")
    parsed = parser.parse_args(args)

    df, library_dict = load_inputs(Path(parsed.input), Path(parsed.library_dict))

    output_dir = Path(parsed.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for col1, col2 in combinations(_cycle_cols(library_dict), 2):
        name = col1 + col2
        disynthon_counts(df, col1, col2, library_dict).write_parquet(output_dir / f"disynthons_{name}.parquet")


if __name__ == "__main__":
    main()
