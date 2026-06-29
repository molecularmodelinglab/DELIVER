"""Join singleton and disynthon enrichment tables into a single enriched table."""

import argparse
import re
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import (
    CORRECTED_COUNT_SUM, LIBRARY_ID, LINE_SIZE, LINE_STRENGTH, LINE_STRENGTH_STD,
    POLYO, RAW_READS_SUM, Z_SCORE_GLOBAL, Z_SCORE_LIB,
)

_METRIC_COLS = [CORRECTED_COUNT_SUM, RAW_READS_SUM, LINE_SIZE, LINE_STRENGTH, LINE_STRENGTH_STD, Z_SCORE_LIB, Z_SCORE_GLOBAL, POLYO]
_SMILES = "SMILES"


def _pair_from_path(path: Path) -> str:
    """Extract cycle pair name from disynthon filename, e.g. 'disynthon_AB.parquet' → 'AB'."""
    m = re.match(r"disynthon_([A-Z]+)\.parquet$", path.name)
    if not m:
        raise ValueError(f"Cannot determine cycle pair from filename: {path.name}")
    return m.group(1)


def join(singletons: pl.DataFrame, disynthon_files: list[Path]) -> pl.DataFrame:
    result = singletons
    for path in sorted(disynthon_files, key=lambda p: p.name):
        pair = _pair_from_path(path)
        prefix = pair.lower() + "_"
        join_cols = [LIBRARY_ID] + list(pair)

        df_dis = pl.read_parquet(path)
        metric_cols = [c for c in _METRIC_COLS if c in df_dis.columns]
        df_dis = df_dis.select(join_cols + metric_cols).rename(
            {c: prefix + c for c in metric_cols}
        )

        result = result.join(df_dis, on=join_cols, how="left")

    return result


def smiles_duplicates(enriched: pl.DataFrame) -> pl.DataFrame | None:
    """Return rows where SMILES appears more than once, sorted by SMILES. None if no SMILES or no duplicates."""
    if _SMILES not in enriched.columns:
        return None
    dupes = enriched.filter(pl.col(_SMILES).is_not_null() & pl.col(_SMILES).is_duplicated())
    if len(dupes) == 0:
        return None
    return dupes.sort(_SMILES)


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",      required=True,        help="Singletons parquet file.")
    parser.add_argument("--disynthons", required=True, nargs="+", help="Disynthon parquet files.")
    parser.add_argument("--output",     required=True,        help="Output enriched parquet file.")
    parsed = parser.parse_args(args)

    input_path = Path(parsed.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    singletons = pl.read_parquet(input_path)
    disynthon_files = [Path(p) for p in parsed.disynthons]
    enriched = join(singletons, disynthon_files)
    enriched.write_parquet(parsed.output)

    dupes = smiles_duplicates(enriched)
    if dupes is not None:
        output_path = Path(parsed.output)
        dupes_path = output_path.with_name(output_path.stem + "_duplicates.parquet")
        dupes.write_parquet(dupes_path)


if __name__ == "__main__":
    main()
