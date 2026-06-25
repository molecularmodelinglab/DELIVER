"""Calculate enrichment scores from normalized DEL counts."""

import argparse
import math
from pathlib import Path

import polars as pl

from deliver.postprocess.columns import CORRECTED_COUNT, LIBRARY_ID, POLYO, RAW_COUNT, Z_SCORE_GLOBAL, Z_SCORE_LIB
from deliver.postprocess.common import load_inputs
from deliver.postprocess.metrics import PolyO, z_score


def enrichment(df: pl.DataFrame, library_dict: dict) -> pl.DataFrame:
    n_possible_total = sum(math.prod(lib.values()) for lib in library_dict.values())
    d = df[RAW_COUNT].sum() / n_possible_total

    lib_results = []
    for lib_id, lib in library_dict.items():
        df_lib = df.filter(pl.col(LIBRARY_ID) == lib_id)
        if len(df_lib) == 0:
            continue
        n = math.prod(lib.values())
        polyo = PolyO(d, df_lib[RAW_COUNT].sum(), df_lib.filter(pl.col(RAW_COUNT) > 0).height, n, n)
        lib_results.append(
            df_lib.with_columns(
                z_score(df_lib[CORRECTED_COUNT], n).alias(Z_SCORE_LIB),
                polyo.score(polyo.raw(df_lib[CORRECTED_COUNT])).alias(POLYO),
            )
        )
    df_result = pl.concat(lib_results)
    return df_result.with_columns(z_score(df_result[CORRECTED_COUNT], n_possible_total).alias(Z_SCORE_GLOBAL))


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
