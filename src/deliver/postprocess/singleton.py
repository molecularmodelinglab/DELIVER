"""Calculate enrichment scores from normalized DEL counts."""

import argparse
import math
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import CORRECTED_COUNT, LIBRARY_ID, POLYO, Z_SCORE, Z_SCORE_GLOBAL, Z_SCORE_LIB
from deliver.postprocess.lib.common import load_inputs
from deliver.postprocess.lib.metrics import PolyO, z_score


def enrichment(df: pl.DataFrame, library_dict: dict) -> pl.DataFrame:
    has_z_score = Z_SCORE in df.columns

    n_possible_total = sum(math.prod(lib.values()) for lib in library_dict.values())
    d = df[CORRECTED_COUNT].sum() / n_possible_total

    lib_results = []
    for lib_id, lib in library_dict.items():
        df_lib = df.filter(pl.col(LIBRARY_ID) == lib_id)
        if len(df_lib) == 0:
            continue
        n = math.prod(lib.values())
        exprs = []
        if not has_z_score:
            exprs.append(z_score(df_lib[CORRECTED_COUNT], n).alias(Z_SCORE_LIB))
        polyo_calc = PolyO(d, df_lib[CORRECTED_COUNT].sum(), df_lib.height, n, n)
        exprs.append(polyo_calc.score(polyo_calc.raw(df_lib[CORRECTED_COUNT])).alias(POLYO))
        lib_results.append(df_lib.with_columns(exprs) if exprs else df_lib)

    df_result = pl.concat(lib_results)
    if not has_z_score:
        df_result = df_result.with_columns(z_score(df_result[CORRECTED_COUNT], n_possible_total).alias(Z_SCORE_GLOBAL))
    return df_result


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
