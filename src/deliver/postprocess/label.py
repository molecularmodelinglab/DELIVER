"""Apply labeling criteria to enriched DEL compounds."""

import argparse
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import CORRECTED_COUNT, POLYO, Z_SCORE, Z_SCORE_GLOBAL, Z_SCORE_LIB
from deliver.postprocess.join import smiles_duplicates

_COUNT_THRESHOLD          = 5
_ZSCORE_THRESHOLD         = 1.0
_POLYO_SINGLETON_THRESHOLD = 4.0
_POLYO_DISYNTHON_THRESHOLD = 4.0


def _any_disynthon(df: pl.DataFrame, col_suffix: str, threshold: float) -> pl.Expr:
    """OR over all disynthon columns whose name ends with _{col_suffix} > threshold."""
    cols = [c for c in df.columns if c.endswith("_" + col_suffix)]
    if not cols:
        return pl.lit(False)
    return pl.any_horizontal(*[pl.col(c) > threshold for c in cols])


def label_count(df: pl.DataFrame) -> pl.Expr:
    return pl.col(CORRECTED_COUNT) > _COUNT_THRESHOLD


def label_count_zscore(df: pl.DataFrame) -> pl.Expr:
    return (
        (pl.col(CORRECTED_COUNT) > _COUNT_THRESHOLD)
        & (pl.col(Z_SCORE) > _ZSCORE_THRESHOLD)
    )


def label_count_zscore_lib(df: pl.DataFrame) -> pl.Expr:
    return (
        (pl.col(CORRECTED_COUNT) > _COUNT_THRESHOLD)
        & ((pl.col(Z_SCORE_LIB) > _ZSCORE_THRESHOLD) | _any_disynthon(df, Z_SCORE_LIB, _ZSCORE_THRESHOLD))
    )


def label_count_zscore_global(df: pl.DataFrame) -> pl.Expr:
    return (
        (pl.col(CORRECTED_COUNT) > _COUNT_THRESHOLD)
        & ((pl.col(Z_SCORE_GLOBAL) > _ZSCORE_THRESHOLD) | _any_disynthon(df, Z_SCORE_GLOBAL, _ZSCORE_THRESHOLD))
    )


def label_count_polyo(df: pl.DataFrame) -> pl.Expr:
    return (
        (pl.col(CORRECTED_COUNT) > _COUNT_THRESHOLD)
        & ((pl.col(POLYO) > _POLYO_SINGLETON_THRESHOLD) | _any_disynthon(df, POLYO, _POLYO_DISYNTHON_THRESHOLD))
    )


MODES: dict[str, callable] = {
    "count":               label_count,
    "count_zscore":        label_count_zscore,
    "count_zscore_lib":    label_count_zscore_lib,
    "count_zscore_global": label_count_zscore_global,
    "count_polyo":         label_count_polyo,
}

_MODE_REQUIRED_COLS: dict[str, list[str]] = {
    "count":               [CORRECTED_COUNT],
    "count_zscore":        [CORRECTED_COUNT, Z_SCORE],
    "count_zscore_lib":    [CORRECTED_COUNT, Z_SCORE_LIB],
    "count_zscore_global": [CORRECTED_COUNT, Z_SCORE_GLOBAL],
    "count_polyo":         [CORRECTED_COUNT, POLYO],
}


def label(df: pl.DataFrame, modes: list[str]) -> pl.DataFrame:
    for mode in modes:
        if mode not in MODES:
            raise ValueError(f"Unknown labeling mode: {mode!r}. Available: {list(MODES)}")
        missing = [c for c in _MODE_REQUIRED_COLS[mode] if c not in df.columns]
        if missing:
            raise ValueError(
                f"Labeling mode '{mode}' requires column(s) {missing} which are missing from the input table."
            )
        df = df.with_columns(MODES[mode](df).alias(f"label_{mode}"))
    return df


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",  required=True,                         help="Enriched parquet file.")
    parser.add_argument("--modes",  required=True, nargs="+",
                        choices=list(MODES),                               help="Labeling modes to apply.")
    parser.add_argument("--output", required=True,                         help="Output labeled parquet file.")
    parsed = parser.parse_args(args)

    input_path = Path(parsed.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        labeled = label(pl.read_parquet(input_path), parsed.modes)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    labeled.write_parquet(parsed.output)

    dupes = smiles_duplicates(labeled)
    if dupes is not None:
        output_path = Path(parsed.output)
        dupes.write_parquet(output_path.with_name(output_path.stem + "_duplicates.parquet"))


if __name__ == "__main__":
    main()
