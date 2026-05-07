"""Compute disynthon counts from normalized DEL counts."""

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

import polars as pl

from deliver.postprocess.common import validate_common_format


def _cycle_cols(library_dict: dict) -> list[str]:
    """Sorted cycle column names present in the library dict (e.g. ['A', 'B', 'C'])."""
    return sorted({k for lib in library_dict.values() for k in lib})


def disynthon_counts(df: pl.DataFrame, col1: str, col2: str) -> pl.DataFrame:
    """Sum corrected_count grouped by (library_id, col1, col2)."""
    return (
        df
        .filter(pl.col(col1).is_not_null() & pl.col(col2).is_not_null())
        .group_by(["library_id", col1, col2])
        .agg(pl.col("corrected_count").sum(), pl.col("raw_count").sum())
        .sort(["library_id", col1, col2])
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
        disynthon_counts(df, col1, col2).write_parquet(output_dir / f"disynthons_{name}.parquet")


if __name__ == "__main__":
    main()
