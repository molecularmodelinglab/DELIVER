"""Add SMILES column to compounds via per-library parquet join."""

import argparse
import json
import sys
from pathlib import Path

import duckdb
import polars as pl

from deliver.postprocess.lib.columns import LIBRARY_ID

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
    max_missing_fraction: float = 0.01,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Add SMILES column by DuckDB join against per-library parquet files.

    If library is given, process only that library (used for parallel execution).
    A compound with no SMILES match (or a corrupted one) gets a null SMILES and is
    otherwise kept, as long as the fraction of such compounds in its library stays
    at or below max_missing_fraction — that's expected decode noise. Above that
    fraction, raises ValueError instead, since it likely signals a real problem
    (reference/decode mismatch) rather than noise.

    Returns (result, report) — report has one row per covered library with
    n_compounds/n_missing/n_corrupted/missing_fraction, for visibility into
    coverage even when nothing crosses the fail threshold.
    """
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
        missing = joined.filter(pl.col(smiles_col).is_null())["compound_id"].to_list()
        corrupted = joined.filter(pl.col(smiles_col).str.contains("\x00"))["compound_id"].to_list()
        bad = missing + corrupted
        fraction = len(bad) / len(df_lib)
        if fraction > max_missing_fraction:
            raise ValueError(
                f"Library {lib_id}: {len(bad)} compound(s) ({fraction:.2%}) have missing or corrupted "
                f"SMILES, above the {max_missing_fraction:.2%} tolerance "
                f"({len(missing)} null, {len(corrupted)} null-byte): "
                f"{bad[:5]}{'...' if len(bad) > 5 else ''}"
            )
        if bad:
            print(
                f"Warning: library {lib_id}: {len(bad)} compound(s) ({fraction:.2%}) have missing or "
                f"corrupted SMILES — treating as decode noise, SMILES set to null: "
                f"{bad[:5]}{'...' if len(bad) > 5 else ''}",
                file=sys.stderr,
            )
            joined = joined.with_columns(
                pl.when(pl.col("compound_id").is_in(bad)).then(None).otherwise(pl.col(smiles_col)).alias(smiles_col)
            )
        report_rows.append({
            "library_id": lib_id,
            "n_compounds": len(df_lib),
            "n_missing": len(missing),
            "n_corrupted": len(corrupted),
            "missing_fraction": fraction,
        })
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
    parser.add_argument(
        "--max-missing-fraction", type=float, default=0.01,
        help="Fail if more than this fraction of a library's compounds have missing/corrupted SMILES (default: 0.01)",
    )
    parser.add_argument("--output",       required=True,  help="Output parquet file")
    parser.add_argument("--report",       default=None,   help="Output per-library SMILES coverage report parquet")
    parsed = parser.parse_args(args)

    with open(parsed.smiles_map) as f:
        smiles_files = json.load(f)

    df = pl.read_parquet(parsed.input)
    result, report = add_smiles(
        df, smiles_files, parsed.compound_col, parsed.smiles_col, parsed.library, parsed.max_missing_fraction
    )
    result.write_parquet(parsed.output)
    if parsed.report:
        report.write_parquet(parsed.report)


if __name__ == "__main__":
    main()
