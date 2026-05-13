"""Calculate enrichment scores from normalized DEL counts."""

import argparse
import math
from pathlib import Path

import polars as pl

from deliver.postprocess.common import add_z_score_norm, load_inputs


def enrichment(df: pl.DataFrame, library_dict: dict) -> pl.DataFrame:
    results = []
    for lib_id, lib in library_dict.items():
        df_lib = df.filter(pl.col("library_id") == lib_id)
        if len(df_lib) == 0:
            continue
        results.append(add_z_score_norm(df_lib, math.prod(lib.values())))
    return pl.concat(results)


def main(args=None):
    parser = argparse.ArgumentParser(description="Calculate enrichment scores from normalized DEL counts.")
    parser.add_argument("--input",        required=True, help="Input normalized parquet file.")
    parser.add_argument("--output",       required=True, help="Output enrichment parquet file.")
    parser.add_argument("--library-dict", required=True, help="Library dictionary JSON file.")
    parsed = parser.parse_args(args)

    df, library_dict = load_inputs(Path(parsed.input), Path(parsed.library_dict))
    enrichment(df, library_dict).write_parquet(parsed.output)


if __name__ == "__main__":
    main()
