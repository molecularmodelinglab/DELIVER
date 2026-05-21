"""Add SMILES column to compounds via per-library sorted parquet lookup."""

import argparse
import json
from pathlib import Path

import polars as pl

from deliver.postprocess.columns import LIBRARY_ID


def add_smiles(
    df: pl.DataFrame,
    smiles_files: dict[str, str],
    compound_col: str,
    smiles_col: str,
) -> pl.DataFrame:
    """Add SMILES column by lazy lookup from per-library sorted parquet files.

    Raises ValueError if any compound in a covered library has no SMILES match.
    compound_col - column name for compound ID in the smiles parquet files
    smiles_col   - column name for SMILES in the smiles parquet files
    """
    results = []
    covered_libs = set(smiles_files.keys())

    for lib_id, file_path in smiles_files.items():
        df_lib = df.filter(pl.col(LIBRARY_ID) == lib_id)
        if len(df_lib) == 0:
            continue
        needed = df_lib["compound_id"].to_list()
        smiles_df = (
            pl.scan_parquet(file_path)
            .filter(pl.col(compound_col).is_in(needed))
            .select([pl.col(compound_col).alias("compound_id"), pl.col(smiles_col)])
            .collect()
        )
        joined = df_lib.join(smiles_df, on="compound_id", how="left")
        missing = joined.filter(pl.col(smiles_col).is_null())["compound_id"].to_list()
        if missing:
            raise ValueError(
                f"Library {lib_id}: {len(missing)} compound(s) have no SMILES match: "
                f"{missing[:5]}{'...' if len(missing) > 5 else ''}"
            )
        results.append(joined)

    uncovered = df.filter(~pl.col(LIBRARY_ID).is_in(covered_libs))
    if len(uncovered) > 0:
        results.append(uncovered.with_columns(pl.lit(None).cast(pl.String).alias(smiles_col)))

    return pl.concat(results)


def main(args=None):
    parser = argparse.ArgumentParser(description="Add SMILES to normalized compounds.")
    parser.add_argument("--input",        required=True, help="Input parquet file")
    parser.add_argument("--smiles-map",   required=True, help='JSON file: {"lib_id": "file_path", ...}')
    parser.add_argument("--compound-col", default="compound", help="Compound ID column in SMILES files (default: compound)")
    parser.add_argument("--smiles-col",   default="SMILES",   help="SMILES column name (default: SMILES)")
    parser.add_argument("--output",       required=True, help="Output parquet file")
    parsed = parser.parse_args(args)

    with open(parsed.smiles_map) as f:
        smiles_files = json.load(f)

    df = pl.read_parquet(parsed.input)
    add_smiles(df, smiles_files, parsed.compound_col, parsed.smiles_col).write_parquet(parsed.output)


if __name__ == "__main__":
    main()
