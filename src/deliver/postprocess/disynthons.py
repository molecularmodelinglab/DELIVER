"""Compute disynthon counts from normalized DEL counts."""

import argparse
import math
from itertools import combinations
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import (
    CORRECTED_COUNT, LIBRARY_ID, MEAN_COUNT, POLYO, RAW_COUNT, STD_COUNT, TOT_COMPOUNDS,
    Z_SCORE_GLOBAL, Z_SCORE_LIB,
)
from deliver.postprocess.lib.common import load_inputs
from deliver.postprocess.lib.metrics import PolyO, z_score

_POLYO_RAW = "_polyo_raw"


def _cycle_cols(library_dict: dict) -> list[str]:
    """Sorted cycle column names present in the library dict (e.g. ['A', 'B', 'C'])."""
    return sorted({k for lib in library_dict.values() for k in lib})


def _total_disynthons(lib: dict) -> int:
    """Total number of non-overlapping disynthons in a library (sum of all pairwise cycle products)."""
    return sum(a * b for a, b in combinations(lib.values(), 2))


def _aggregate(df: pl.DataFrame, col1: str, col2: str, has_raw: bool) -> pl.DataFrame:
    """Group by library + col1 + col2, summing counts and (if available) raw polyO values."""
    aggs = [
        pl.col(CORRECTED_COUNT).sum(),
        (pl.col(CORRECTED_COUNT) ** 2).sum().alias("_sum_sq"),
    ]
    if has_raw:
        aggs += [pl.col(RAW_COUNT).sum(), pl.col(_POLYO_RAW).sum()]
    return (
        df
        .filter(pl.col(col1).is_not_null() & pl.col(col2).is_not_null())
        .group_by([LIBRARY_ID, col1, col2])
        .agg(aggs)
        .sort([LIBRARY_ID, col1, col2])
    )


def _add_lib_statistics(
    df_lib: pl.DataFrame, lib: dict, col1: str, col2: str, polyo: PolyO | None,
) -> pl.DataFrame:
    """Add tot_compounds, mean_count, std_count, z_score_lib, and polyo for one library.
    polyo is None when raw_count is absent; in that case the polyo column is omitted.
    """
    tot_compounds = math.prod(count for k, count in lib.items() if k not in {col1, col2})  # compounds per disynthon (remaining cycles' product)
    n_disynthons = lib[col1] * lib[col2]  # number of possible disynthons in the library
    df = (
        df_lib
        .with_columns(
            pl.lit(tot_compounds).alias(TOT_COMPOUNDS),
            (pl.col(CORRECTED_COUNT) / tot_compounds).alias(MEAN_COUNT),
        )
        .with_columns(
            ((pl.col("_sum_sq") / tot_compounds - pl.col(MEAN_COUNT) ** 2).sqrt()).alias(STD_COUNT),
        )
        .drop("_sum_sq")
    )
    exprs = [z_score(df[CORRECTED_COUNT], n_disynthons).alias(Z_SCORE_LIB)]
    if polyo is not None:
        exprs.append(polyo.score(df[_POLYO_RAW]).alias(POLYO))
    df = df.with_columns(exprs)
    if polyo is not None:
        df = df.drop(_POLYO_RAW)
    return df


def disynthon_counts(df: pl.DataFrame, col1: str, col2: str, library_dict: dict) -> pl.DataFrame:
    """Aggregate to disynthon level and compute statistics per library, then add global z-score."""
    has_raw = RAW_COUNT in df.columns
    n_possible_total = sum(math.prod(lib.values()) for lib in library_dict.values())
    if has_raw:
        d = df[RAW_COUNT].sum() / n_possible_total

    results = []
    for lib_id, lib in library_dict.items():
        if col1 not in lib or col2 not in lib:
            continue
        df_lib_orig = df.filter(pl.col(LIBRARY_ID) == lib_id)
        if len(df_lib_orig) == 0:
            continue

        n_possible = math.prod(lib.values())
        if has_raw and d:
            n_reads = df_lib_orig[RAW_COUNT].sum()
            n_obs = df_lib_orig.filter(pl.col(RAW_COUNT) > 0).height
            n_features = _total_disynthons(lib)
            polyo = PolyO(d, n_reads, n_obs, n_possible, n_features)
            df_lib_orig = df_lib_orig.with_columns(polyo.raw(df_lib_orig[CORRECTED_COUNT]).alias(_POLYO_RAW))
        else:
            polyo = None

        agg = _aggregate(df_lib_orig, col1, col2, has_raw)
        results.append(_add_lib_statistics(agg, lib, col1, col2, polyo))

    df_result = pl.concat(results)
    n_total_disynthons = sum(
        lib[col1] * lib[col2]
        for lib in library_dict.values()
        if col1 in lib and col2 in lib
    )
    return df_result.with_columns(
        z_score(df_result[CORRECTED_COUNT], n_total_disynthons).alias(Z_SCORE_GLOBAL)
    )


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
