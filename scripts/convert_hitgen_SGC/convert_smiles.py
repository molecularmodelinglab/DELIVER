"""
Convert one SGC-DEL fully-enumerated .txt.gz file to sorted parquet.

Input columns:  CompoundIndex, Smiles  (tab-separated, gzip-compressed)
Output columns: compound, SMILES       (sorted lexicographically by compound)

Conversion is two-phase to avoid DuckDB crashes on very large CSV sorts:
  1. Read CSV → unsorted parquet (streaming, low memory)
  2. Sort parquet in-place using DuckDB with spill-to-disk

Usage:
  python convert_smiles.py --input FILE.txt.gz --output-dir DIR [--temp-dir DIR] [--memory-limit 28GB]
"""

import argparse
import os
import shutil
from pathlib import Path

import duckdb


def _make_conn(temp_dir: str, memory_limit: str) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.execute(f"SET threads={os.cpu_count() or 4}")
    conn.execute(f"SET memory_limit='{memory_limit}'")
    conn.execute(f"SET temp_directory='{temp_dir}'")
    return conn


def convert(input_path: str, output_dir: str, temp_dir: str, memory_limit: str) -> None:
    stem = Path(input_path).name
    for suffix in ("_Fully_Enumerated_Structures.txt.gz", ".txt.gz"):
        if stem.endswith(suffix):
            lib_name = stem[: -len(suffix)]
            break
    else:
        lib_name = stem.split(".")[0]

    output_path = Path(output_dir) / f"{lib_name}_enumerated.parquet"
    tmp_path = Path(temp_dir) / f"_csv_{lib_name}.parquet"

    # Phase 1: CSV → unsorted parquet (no ORDER BY, safe for any size)
    print(f"Reading {input_path} ...", flush=True)
    conn = _make_conn(temp_dir, memory_limit)
    conn.execute(f"""
        COPY (
            SELECT CompoundIndex AS compound, Smiles AS "SMILES"
            FROM read_csv('{input_path}', delim='\\t', header=true,
                          compression='gzip', ignore_errors=true)
        ) TO '{tmp_path}' (FORMAT PARQUET, ROW_GROUP_SIZE 122880)
    """)
    conn.close()

    # Phase 2: sort parquet in-place
    print(f"Sorting ...", flush=True)
    conn = _make_conn(temp_dir, memory_limit)
    conn.execute(f"""
        COPY (
            SELECT compound, "SMILES"
            FROM read_parquet('{tmp_path}')
            ORDER BY compound
        ) TO '{output_path}' (FORMAT PARQUET, ROW_GROUP_SIZE 122880)
    """)
    conn.close()

    tmp_path.unlink()
    print(f"Written: {output_path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Convert SGC-DEL .txt.gz to sorted parquet.")
    parser.add_argument("--input",        required=True,  help="Input .txt.gz file")
    parser.add_argument("--output-dir",   required=True,  help="Output directory")
    parser.add_argument("--temp-dir",     default=None,   help="Temp directory (default: $TMPDIR or output-dir)")
    parser.add_argument("--memory-limit", default="28GB", help="DuckDB memory limit (default: 28GB)")
    args = parser.parse_args()

    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    temp_dir = args.temp_dir or os.environ.get("TMPDIR") or args.output_dir
    convert(args.input, args.output_dir, temp_dir, args.memory_limit)


if __name__ == "__main__":
    main()
