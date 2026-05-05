"""Calculate enrichment scores from deduplicated/aggregated DEL counts."""

import argparse
import sys
from pathlib import Path

import polars as pl


def main(args=None):
    """Calculate enrichment scores from deduplicated/aggregated DEL counts."""
    parser = argparse.ArgumentParser(description="Calculate enrichment scores from deduplicated/aggregated DEL counts.")
    parser.add_argument("--input",         required=True,               help="Input deduplicated/aggregated parquet file.")
    parser.add_argument("--output",        required=True,               help="Output enrichment parquet file.")
    parser.add_argument("--deli-data-dir", required=False, default=None, help="Path to DELi data directory (libraries, building blocks).")
    parsed = parser.parse_args(args)

    input_path = Path(parsed.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    df = pl.read_parquet(input_path)

    # TODO: implement enrichment scoring logic

    df.write_parquet(parsed.output)


if __name__ == "__main__":
    main()
