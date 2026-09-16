"""Merge per-library SMILES parquets back into a single normalized parquet."""

import argparse
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import LIBRARY_ID


def merge_smiles(
    orig_path: Path,
    partial_paths: list[Path],
    smiles_col: str,
) -> pl.DataFrame:
    """Concatenate per-library parquets (with SMILES) and add null SMILES for uncovered libraries."""
    partials = [pl.read_parquet(p) for p in partial_paths]

    covered_libs = set()
    for df_p in partials:
        covered_libs.update(df_p[LIBRARY_ID].unique().to_list())

    df_orig = pl.read_parquet(orig_path)
    uncovered = df_orig.filter(~pl.col(LIBRARY_ID).is_in(covered_libs))
    if len(uncovered) > 0:
        partials.append(uncovered.with_columns(pl.lit(None).cast(pl.String).alias(smiles_col)))

    return pl.concat(partials)


def merge_smiles_reports(report_paths: list[Path]) -> pl.DataFrame:
    """Concatenate per-library SMILES coverage reports, worst coverage first."""
    return pl.concat([pl.read_parquet(p) for p in report_paths]).sort("missing_fraction", descending=True)


def main(args=None):
    parser = argparse.ArgumentParser(description="Merge per-library SMILES parquets.")
    parser.add_argument("--input",         required=True,  help="Original normalized parquet (for uncovered libraries)")
    parser.add_argument("--partials",      required=True, nargs="+", help="Per-library parquets with SMILES added")
    parser.add_argument("--reports",       default=None, nargs="+", help="Per-library SMILES coverage report parquets")
    parser.add_argument("--smiles-col",    default="SMILES", help="SMILES column name (default: SMILES)")
    parser.add_argument("--output",        required=True,  help="Output merged parquet")
    parser.add_argument("--report-output", default=None,   help="Output merged SMILES coverage report (TSV)")
    parsed = parser.parse_args(args)

    merge_smiles(
        Path(parsed.input),
        [Path(p) for p in parsed.partials],
        parsed.smiles_col,
    ).write_parquet(parsed.output)

    if parsed.reports:
        merge_smiles_reports([Path(p) for p in parsed.reports]).write_csv(parsed.report_output, separator="\t")


if __name__ == "__main__":
    main()
