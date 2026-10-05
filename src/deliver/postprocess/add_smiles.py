"""Add SMILES column to compounds via per-library parquet join."""

import argparse
import json
import sys
from pathlib import Path

import duckdb
import polars as pl

from deliver.postprocess.lib.columns import LIBRARY_ID
from deliver.postprocess.lib.common import validate_compound_ids

_REPORT_SCHEMA = {
    "library_id": pl.String,
    "n_compounds": pl.Int64,
    "n_missing": pl.Int64,
    "n_corrupted": pl.Int64,
    "missing_fraction": pl.Float64,
}


def add_smiles(
    df: pl.DataFrame,
    smiles_files: dict[str, str],
    compound_col: str,
    smiles_col: str,
    library: str | None = None,
    on_missing: str = "fail",
) -> pl.DataFrame:
    """Add SMILES column by DuckDB lookup from per-library sorted parquet files.

    If library is given, process only that library (used for parallel execution).
    on_missing controls compounds with no SMILES match: "fail" raises ValueError,
    "null" keeps the row with a null SMILES, "drop" removes the row. Corrupted
    (null-byte) SMILES always raise — that is file damage, not a coverage gap.
    """
    threads = os.environ.get("POLARS_MAX_THREADS")
    if threads:
        # DuckDB, like polars, defaults to one thread per core on the node rather
        # than per core SLURM granted; keep it to the same allocation.
        duckdb.execute(f"SET threads TO {int(threads)}")

    if library is not None:
        smiles_files = {library: smiles_files[library]} if library in smiles_files else {}
        df = df.filter(pl.col(LIBRARY_ID) == library)

    results = []
    report_rows = []
    covered_libs = set(smiles_files.keys())

    for lib_id, file_path in smiles_files.items():
        df_lib = df.filter(pl.col(LIBRARY_ID) == lib_id)
        if len(df_lib) == 0:
            continue
        needed = df_lib.select("compound_id").unique()
        smiles_df = pl.from_arrow(
            duckdb.execute(f"""
                SELECT s.{compound_col} AS compound_id, s.{smiles_col}
                FROM read_parquet('{file_path}') AS s
                JOIN needed ON needed.compound_id = s.{compound_col}
            """).arrow()
        )
        joined = df_lib.join(smiles_df, on="compound_id", how="left")
        corrupted = joined.filter(pl.col(smiles_col).str.contains("\x00"))["compound_id"].to_list()
        if corrupted:
            raise ValueError(
                f"Library {lib_id}: {len(corrupted)} compound(s) have corrupted (null-byte) "
                f"SMILES: {corrupted[:5]}{'...' if len(corrupted) > 5 else ''}"
            )
        missing = joined.filter(pl.col(smiles_col).is_null())["compound_id"].to_list()
        if missing:
            if on_missing == "fail":
                raise ValueError(
                    f"Library {lib_id}: {len(missing)} compound(s) have missing or corrupted SMILES "
                    f"({len(missing)} null, 0 null-byte): "
                    f"{missing[:5]}{'...' if len(missing) > 5 else ''} "
                    f"— rerun with --on-missing null|drop to tolerate enumeration gaps"
                )
            print(
                f"WARNING: library {lib_id}: {len(missing)} compound(s) not in the SMILES "
                f"file ({'kept with null SMILES' if on_missing == 'null' else 'dropped'}): "
                f"{missing[:5]}{'...' if len(missing) > 5 else ''}",
                file=sys.stderr,
            )
            if on_missing == "drop":
                joined = joined.filter(pl.col(smiles_col).is_not_null())
        results.append(joined)

    uncovered = df.filter(~pl.col(LIBRARY_ID).is_in(covered_libs))
    if len(uncovered) > 0:
        results.append(uncovered.with_columns(pl.lit(None).cast(pl.String).alias(smiles_col)))

    report = pl.DataFrame(report_rows, schema=_REPORT_SCHEMA)
    return pl.concat(results), report


def main(args=None):
    parser = argparse.ArgumentParser(description="Add SMILES to normalized compounds.")
    parser.add_argument("--input",        required=True,  help="Input parquet file")
    parser.add_argument("--smiles-map",   required=True,  help='JSON file: {"lib_id": "file_path", ...}')
    parser.add_argument("--compound-col", default="compound", help="Compound ID column in SMILES files (default: compound)")
    parser.add_argument("--smiles-col",   default="SMILES",   help="SMILES column name (default: SMILES)")
    parser.add_argument("--library",      default=None,   help="Process only this library ID (for parallel execution)")
    parser.add_argument("--on-missing",   default="fail", choices=["fail", "null", "drop"],
                        help="Compounds absent from the SMILES file: fail (default), keep with null SMILES, or drop")
    parser.add_argument("--output",       required=True,  help="Output parquet file")
    parser.add_argument("--report",       default=None,   help="Output per-library SMILES coverage report parquet")
    parsed = parser.parse_args(args)

    with open(parsed.smiles_map) as f:
        smiles_files = json.load(f)

    df = pl.read_parquet(parsed.input)
    add_smiles(
        df, smiles_files, parsed.compound_col, parsed.smiles_col, parsed.library, parsed.on_missing
    ).write_parquet(parsed.output)


if __name__ == "__main__":
    main()
