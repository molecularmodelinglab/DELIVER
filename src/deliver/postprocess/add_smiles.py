"""Add SMILES column to compounds via per-library parquet join."""

import argparse
import json
from pathlib import Path

import duckdb
import polars as pl

from deliver.postprocess.lib.columns import LIBRARY_ID


def add_smiles(
    df: pl.DataFrame,
    smiles_files: dict[str, str],
    compound_col: str,
    smiles_col: str,
    library: str | None = None,
) -> pl.DataFrame:
    """Add SMILES column by DuckDB join against per-library parquet files.

    If library is given, process only that library (used for parallel execution).
    Raises ValueError if any compound in a covered library has no SMILES match.
    """
    if library is not None:
        smiles_files = {library: smiles_files[library]} if library in smiles_files else {}
        df = df.filter(pl.col(LIBRARY_ID) == library)

    results = []
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
        missing = joined.filter(pl.col(smiles_col).is_null())["compound_id"].to_list()
        corrupted = joined.filter(pl.col(smiles_col).str.contains("\x00"))["compound_id"].to_list()
        bad = missing + corrupted
        if bad:
            raise ValueError(
                f"Library {lib_id}: {len(bad)} compound(s) have missing or corrupted SMILES "
                f"({len(missing)} null, {len(corrupted)} null-byte): "
                f"{bad[:5]}{'...' if len(bad) > 5 else ''}"
            )
        results.append(joined)

    uncovered = df.filter(~pl.col(LIBRARY_ID).is_in(covered_libs))
    if len(uncovered) > 0:
        results.append(uncovered.with_columns(pl.lit(None).cast(pl.String).alias(smiles_col)))

    return pl.concat(results)


def main(args=None):
    parser = argparse.ArgumentParser(description="Add SMILES to normalized compounds.")
    parser.add_argument("--input",        required=True,  help="Input parquet file")
    parser.add_argument("--smiles-map",   required=True,  help='JSON file: {"lib_id": "file_path", ...}')
    parser.add_argument("--compound-col", default="compound", help="Compound ID column in SMILES files (default: compound)")
    parser.add_argument("--smiles-col",   default="SMILES",   help="SMILES column name (default: SMILES)")
    parser.add_argument("--library",      default=None,   help="Process only this library ID (for parallel execution)")
    parser.add_argument("--output",       required=True,  help="Output parquet file")
    parsed = parser.parse_args(args)

    with open(parsed.smiles_map) as f:
        smiles_files = json.load(f)

    df = pl.read_parquet(parsed.input)
    add_smiles(df, smiles_files, parsed.compound_col, parsed.smiles_col, parsed.library).write_parquet(parsed.output)


if __name__ == "__main__":
    main()
