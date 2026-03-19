"""Deduplicate and aggregate DEL counts."""

import click
import polars as pl


@click.command()
@click.option("--input",         "input_path",    required=True,  type=click.Path(exists=True), help="Input counts parquet file.")
@click.option("--output",        "output_path",   required=True,  type=click.Path(),            help="Output deduplicated/aggregated parquet file.")
@click.option("--deli-data-dir", "deli_data_dir", required=False, type=click.Path(),            help="Path to DELi data directory (libraries, building blocks).")
def deduplicate(input_path: str, output_path: str, deli_data_dir: str | None) -> None:
    """Deduplicate and aggregate DEL counts parquet file."""
    df = pl.read_parquet(input_path)

    # TODO: implement deduplication logic
    # TODO: implement aggregation logic

    df.write_parquet(output_path)


if __name__ == "__main__":
    deduplicate()
