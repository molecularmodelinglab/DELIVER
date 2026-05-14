"""Calculate enrichment scores from normalized DEL counts."""

import argparse
import math
from pathlib import Path

import polars as pl

from deliver.postprocess.columns import CORRECTED_COUNT, LIBRARY_ID, Z_SCORE_GLOBAL, Z_SCORE_LIB
from deliver.postprocess.common import load_inputs
from deliver.postprocess.metrics import z_score


def enrichment(df: pl.DataFrame, library_dict: dict) -> pl.DataFrame:
    lib_results = []
    for lib_id, lib in library_dict.items():
        df_lib = df.filter(pl.col(LIBRARY_ID) == lib_id)
        if len(df_lib) == 0:
            continue
        n = math.prod(lib.values())  # number of possible compounds in the library
        lib_results.append(
            df_lib.with_columns(z_score(df_lib[CORRECTED_COUNT], n).alias(Z_SCORE_LIB))
        )
    df_result = pl.concat(lib_results)
    # number of possible compounds in the library pool
    n_total = sum(math.prod(lib.values()) for lib in library_dict.values())
    return df_result.with_columns(z_score(df_result[CORRECTED_COUNT], n_total).alias(Z_SCORE_GLOBAL))


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
