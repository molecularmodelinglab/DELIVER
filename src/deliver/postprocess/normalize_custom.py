"""Normalize an externally-provided counts file to DELIVER common format.

Three ways to specify compound identity (exactly one required):
  --compound-col COL            column already in "library-bb1-bb2-..." format
  --library-col + --bb-ids-col  library_id column + comma-separated bb_ids column
  --library-col + --cycle-cols  library_id column + individual bb_id columns
"""

import argparse
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import COMPOUND_ID, CORRECTED_COUNT, LIBRARY_ID, RAW_READS, Z_SCORE
from deliver.postprocess.lib.common import validate_common_format

_SMILES = "SMILES"


def _parse_compound_col(df: pl.DataFrame, compound_col: str, num_cycles: int = 3) -> pl.DataFrame:
    """Rename compound column and derive library_id + cycle columns.

    Splits from the right exactly num_cycles times so library names that contain
    '-' (e.g. "LIB-1") are preserved correctly.
    """
    df = df.rename({compound_col: COMPOUND_ID})
    parts = pl.col(COMPOUND_ID).str.split("-")
    df = df.with_columns([
        parts.list.slice(0, parts.list.len() - num_cycles).list.join("-").alias(LIBRARY_ID),
        *[parts.list.get(-(num_cycles - i)).alias(chr(ord("A") + i)) for i in range(num_cycles)],
    ])
    return df


def _parse_library_bb_ids(df: pl.DataFrame, library_col: str, bb_ids_col: str) -> pl.DataFrame:
    """Build compound_id and cycle columns from library_id + comma-separated bb_ids."""
    df = df.rename({library_col: LIBRARY_ID})
    parts = df[bb_ids_col].str.split(",")
    n_cycles = len(df[bb_ids_col][0].split(","))
    for i in range(n_cycles):
        df = df.with_columns(parts.list.get(i).alias(chr(ord("A") + i)))
    df = df.with_columns(
        (pl.col(LIBRARY_ID) + "-" + df[bb_ids_col].str.replace_all(",", "-")).alias(COMPOUND_ID)
    ).drop(bb_ids_col)
    return df


def _parse_library_cycles(df: pl.DataFrame, library_col: str, cycle_cols: list[str]) -> pl.DataFrame:
    """Build compound_id from library_id + individual cycle columns, renaming cycles to A, B, C..."""
    df = df.rename({library_col: LIBRARY_ID})
    cycle_names = [chr(ord("A") + i) for i in range(len(cycle_cols))]
    rename_map = {old: new for old, new in zip(cycle_cols, cycle_names) if old != new}
    if rename_map:
        df = df.rename(rename_map)
    cycle_exprs = [pl.col(name).cast(pl.Utf8) for name in cycle_names]
    return df.with_columns(
        pl.concat_str([pl.col(LIBRARY_ID)] + cycle_exprs, separator="-").alias(COMPOUND_ID)
    )


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input",  required=True, help="Input parquet file.")
    parser.add_argument("--output", required=True, help="Output normalized parquet file.")

    parser.add_argument("--compound-col", help="Column already in library-bb1-bb2-... format.")
    parser.add_argument("--num-cycles",   type=int, default=3,
                        help="Number of BB cycles when using --compound-col. Default: 3. "
                             "Must be set explicitly if the library name contains '-'.")
    parser.add_argument("--library-col",  help="Library ID column (used with --bb-ids-col or --cycle-cols).")
    parser.add_argument("--bb-ids-col",   help="Comma-separated bb IDs column (used with --library-col).")
    parser.add_argument("--cycle-cols",   nargs="+", help="Individual bb ID columns in cycle order (used with --library-col).")

    parser.add_argument("--corrected-count-col", required=True, help="UMI-corrected count column name.")
    parser.add_argument("--raw-count-col",        help="Raw read count column (optional; required for PolyO).")
    parser.add_argument("--z-score-col",          help="Pre-calculated z-score column (carried through; z-score will not be recalculated).")
    parser.add_argument("--smiles-col",           help="SMILES column name.")
    parsed = parser.parse_args(args)

    has_compound   = bool(parsed.compound_col)
    has_lib_bb     = bool(parsed.library_col and parsed.bb_ids_col)
    has_lib_cycles = bool(parsed.library_col and parsed.cycle_cols)
    if sum([has_compound, has_lib_bb, has_lib_cycles]) != 1:
        print(
            "Error: specify exactly one compound identity mode:\n"
            "  --compound-col, or\n"
            "  --library-col + --bb-ids-col, or\n"
            "  --library-col + --cycle-cols",
            file=sys.stderr,
        )
        sys.exit(1)

    inp = Path(parsed.input)
    if not inp.exists():
        print(f"Error: input file not found: {inp}", file=sys.stderr)
        sys.exit(1)

    df = pl.read_parquet(inp)

    if has_compound:
        df = _parse_compound_col(df, parsed.compound_col, parsed.num_cycles)
    elif has_lib_bb:
        df = _parse_library_bb_ids(df, parsed.library_col, parsed.bb_ids_col)
    else:
        df = _parse_library_cycles(df, parsed.library_col, parsed.cycle_cols)

    df = df.rename({parsed.corrected_count_col: CORRECTED_COUNT})
    if parsed.raw_count_col:
        df = df.rename({parsed.raw_count_col: RAW_READS})
    if parsed.z_score_col:
        df = df.rename({parsed.z_score_col: Z_SCORE})
    if parsed.smiles_col and parsed.smiles_col != _SMILES:
        df = df.rename({parsed.smiles_col: _SMILES})

    cycle_cols = [c for c in df.columns if len(c) == 1 and c.isupper()]
    optional = [c for c in [RAW_READS, Z_SCORE, "SMILES"] if c in df.columns]
    df = df.select([COMPOUND_ID, LIBRARY_ID] + cycle_cols + [CORRECTED_COUNT] + optional)

    validate_common_format(df)
    df.write_parquet(Path(parsed.output))


if __name__ == "__main__":
    main()
