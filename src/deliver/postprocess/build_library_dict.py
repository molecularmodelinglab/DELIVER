"""Build library dictionary JSON from DELi building blocks directory."""

import argparse
import csv
import json
import sys
from pathlib import Path


def _bb_position_key(cycle: int) -> str:
    """1 → A, 2 → B, 3 → C, ..."""
    return chr(ord("A") + cycle - 1)


def _count_real_building_blocks(bb_csv: Path) -> int:
    """Count building blocks from a CSV with columns id,tag (multiple rows per id).

    IDs are sequential integers; null blocks have 'null' in the id.
    Returns the maximum non-null id, which equals the number of real building blocks.
    """
    with bb_csv.open() as f:
        reader = csv.DictReader(f)
        ids = [int(row["id"]) for row in reader if "null" not in row["id"].lower()]
    return max(ids) if ids else 0


def main(args=None):
    """Build library dictionary JSON: {library_id: {A: count, B: count, ...}}."""
    parser = argparse.ArgumentParser(description="Build library dictionary JSON from DELi building blocks directory.")
    parser.add_argument("--deli-data-dir", required=True, help="DELi data directory (must contain libraries/ and building_blocks/ subdirs).")
    parser.add_argument("--output",        required=True, help="Output JSON file.")
    parsed = parser.parse_args(args)

    deli_data = Path(parsed.deli_data_dir)
    if not deli_data.exists():
        print(f"Error: deli-data-dir not found: {deli_data}", file=sys.stderr)
        sys.exit(1)

    libraries_dir = deli_data / "libraries"
    bb_dir = deli_data / "building_blocks"

    result = {}
    for lib_json in sorted(libraries_dir.glob("*.json")):
        lib_id = lib_json.stem
        data = json.loads(lib_json.read_text())
        lib_dict = {}
        for bb_set in data.get("bb_sets", []):
            bb_set_name = bb_set["bb_set_name"]
            count = _count_real_building_blocks(bb_dir / f"{bb_set_name}.csv")
            lib_dict[_bb_position_key(bb_set["cycle"])] = count
        result[lib_id] = lib_dict

    Path(parsed.output).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
