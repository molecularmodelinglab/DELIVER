"""Merge per-library SMILES parquets back into a single normalized parquet."""

import argparse
import sys
from pathlib import Path

import polars as pl

from deliver.postprocess.lib.columns import LIBRARY_ID
from deliver.postprocess.lib.common import sink_parquet, validate_compound_ids


def merge_smiles(
    orig_path: Path,
    partial_paths: list[Path],
    smiles_col: str,
) -> pl.LazyFrame:
    """Concatenate per-library parquets (with SMILES) and add null SMILES for uncovered libraries.

    Returns a lazy query for the caller to stream to disk (sink_parquet): the
    partials together hold every compound of the run (328M rows for TREX1), and
    loading them plus the full normalized parquet into memory ran a 64 GB VM out
    of RAM. The ID checks stream too; only each partial's library ids are loaded.
    """
    partials = [pl.scan_parquet(p) for p in partial_paths]

    covered_libs = set()
    for path, lf_p in zip(partial_paths, partials):
        validate_compound_ids(lf_p, f"merge_smiles partial {Path(path).name}")
        covered_libs.update(lf_p.select(pl.col(LIBRARY_ID).unique()).collect()[LIBRARY_ID].to_list())

    lf_orig = pl.scan_parquet(orig_path)
    if smiles_col in lf_orig.collect_schema().names():
        # The input is NORMALIZE's output and never has SMILES. If it does, an
        # earlier MERGE_SMILES wrote over it (a work dir from before the
        # stageAs fix), and nothing downstream of it can be trusted.
        raise ValueError(
            f"{orig_path} already has a {smiles_col!r} column: it was overwritten by an "
            "earlier merge. Rerun postprocessing in a fresh work directory."
        )
    uncovered = lf_orig.filter(~pl.col(LIBRARY_ID).is_in(list(covered_libs)))
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
    sink_parquet(merged, parsed.output)

    if parsed.reports:
        merge_smiles_reports([Path(p) for p in parsed.reports]).write_csv(parsed.report_output, separator="\t")


if __name__ == "__main__":
    main()
