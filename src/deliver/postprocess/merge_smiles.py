"""Merge per-library SMILES parquets back into a single normalized parquet."""

import argparse
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import LIBRARY_ID
from deliver.postprocess.lib.common import validate_compound_ids


def merge_smiles(
    orig_path: Path,
    partial_paths: list[Path],
    smiles_col: str,
) -> pl.DataFrame:
    """Concatenate per-library parquets (with SMILES) and add null SMILES for uncovered libraries."""
    partials = [pl.read_parquet(p) for p in partial_paths]
    for path, df_p in zip(partial_paths, partials):
        validate_compound_ids(df_p, f"merge_smiles partial {Path(path).name}")

    covered_libs = set()
    for df_p in partials:
        covered_libs.update(df_p[LIBRARY_ID].unique().to_list())

    df_orig = pl.read_parquet(orig_path)
    if smiles_col in df_orig.columns:
        # The input is NORMALIZE's output and never has SMILES. If it does, an
        # earlier MERGE_SMILES wrote over it (a work dir from before the
        # stageAs fix), and nothing downstream of it can be trusted.
        raise ValueError(
            f"{orig_path} already has a {smiles_col!r} column: it was overwritten by an "
            "earlier merge. Rerun postprocessing in a fresh work directory."
        )
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

    try:
        merged = merge_smiles(
            Path(parsed.input),
            [Path(p) for p in parsed.partials],
            parsed.smiles_col,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    merged.write_parquet(parsed.output)

    if parsed.reports:
        merge_smiles_reports([Path(p) for p in parsed.reports]).write_csv(parsed.report_output, separator="\t")


if __name__ == "__main__":
    main()
