"""Calculate enrichment scores from normalized DEL counts."""

import argparse
import json
import math
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.common import validate_common_format


def _n_compounds(library_dict: dict) -> int:
    """Total number of possible compounds across all libraries (product of BB counts per library)."""
    total = 0
    for lib in library_dict.values():
        product = 1
        for count in lib.values():
            product *= count
        total += product
    return total


def enrichment(df: pl.DataFrame, library_dict: dict) -> pl.DataFrame:
    n_total = df["corrected_count"].sum()
    n_compounds = _n_compounds(library_dict)
    c_expected = n_total / n_compounds

    denom = math.sqrt(c_expected * (1 - c_expected / n_total))
    norm_factor = math.sqrt(n_total)

    return df.with_columns(
        (
            (pl.col("corrected_count") - c_expected) / denom / norm_factor
        ).alias("z_score_norm")
    )


def main(args=None):
    """Calculate enrichment scores from normalized DEL counts."""
    parser = argparse.ArgumentParser(description="Calculate enrichment scores from normalized DEL counts.")
    parser.add_argument("--input",        required=True, help="Input normalized parquet file.")
    parser.add_argument("--output",       required=True, help="Output enrichment parquet file.")
    parser.add_argument("--library-dict", required=True, help="Library dictionary JSON file.")
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
    df = enrichment(df, library_dict)
    df.write_parquet(parsed.output)


if __name__ == "__main__":
    main()
