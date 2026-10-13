"""Calculate enrichment scores from normalized DEL counts."""

import argparse
import math
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import CORRECTED_COUNT, LIBRARY_ID, POLYO, Z_SCORE, Z_SCORE_GLOBAL, Z_SCORE_LIB
from deliver.postprocess.lib.common import scan_inputs, sink_parquet
from deliver.postprocess.lib.metrics import PolyO, z_score_expr


def enrichment(df: pl.DataFrame, library_dict: dict) -> pl.DataFrame:
    return enrichment_lazy(df.lazy(), library_dict).collect()


def enrichment_lazy(lf: pl.LazyFrame, library_dict: dict) -> pl.LazyFrame:
    """Per-library and global enrichment scores, as a query to stream to disk.

    Rows come out library by library in library_dict order (each library's rows
    in input order), as they always have. The sums the scores need are computed
    in a first pass over the data; the scores themselves are then the same
    element-wise formulas (z_score_expr, PolyO.score_expr), so the values are
    identical to computing them on fully loaded columns.
    """
    has_z_score = Z_SCORE in lf.collect_schema().names()

    n_possible_total = sum(math.prod(lib.values()) for lib in library_dict.values())
    d = lf.select(pl.col(CORRECTED_COUNT).sum()).collect(engine="streaming").item() / n_possible_total

    lib_stats = {
        row[LIBRARY_ID]: row
        for row in lf.group_by(LIBRARY_ID)
        .agg(pl.col(CORRECTED_COUNT).sum().alias("_sum"), pl.len().alias("_height"))
        .collect(engine="streaming")
        .iter_rows(named=True)
    }

    lib_results = []
    covered_total = 0
    for lib_id, lib in library_dict.items():
        stats = lib_stats.get(lib_id)
        if stats is None or stats["_height"] == 0:
            continue
        covered_total += stats["_sum"]
        n = math.prod(lib.values())
        exprs = []
        if not has_z_score:
            exprs.append(z_score_expr(pl.col(CORRECTED_COUNT), stats["_sum"], n).alias(Z_SCORE_LIB))
        polyo_calc = PolyO(d, stats["_sum"], stats["_height"], n, n)
        exprs.append(polyo_calc.score_expr(pl.col(CORRECTED_COUNT)).alias(POLYO))
        lib_results.append(lf.filter(pl.col(LIBRARY_ID) == lib_id).with_columns(exprs))

    lf_result = pl.concat(lib_results)
    if not has_z_score:
        lf_result = lf_result.with_columns(
            z_score_expr(pl.col(CORRECTED_COUNT), covered_total, n_possible_total).alias(Z_SCORE_GLOBAL)
        )
    return lf_result


def main(args=None):
    parser = argparse.ArgumentParser(description="Calculate enrichment scores from normalized DEL counts.")
    parser.add_argument("--input",        required=True, help="Input normalized parquet file.")
    parser.add_argument("--output",       required=True, help="Output enrichment parquet file.")
    parser.add_argument("--library-dict", required=True, help="Library dictionary JSON file.")
    parsed = parser.parse_args(args)

    lf, library_dict = scan_inputs(Path(parsed.input), Path(parsed.library_dict))
    sink_parquet(enrichment_lazy(lf, library_dict), parsed.output)


if __name__ == "__main__":
    main()
