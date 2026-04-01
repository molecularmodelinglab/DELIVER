"""
Convert Hitgen library TSV files to DELi library JSON + building block CSV format.

Each TSV file in --input-dir is converted to:
  <output-dir>/libraries/<lib_name>.json
  <output-dir>/building_blocks/<lib_name>_BBA.csv
  <output-dir>/building_blocks/<lib_name>_BBB.csv
  <output-dir>/building_blocks/<lib_name>_BBC.csv

Usage:
  python convert_hitgen.py --input-dir DIR --output-dir DIR --config CONFIG

See library_config_example.yml for config format.
"""

import argparse
import json
import os
import sys

import pandas as pd
import yaml


# ---------------------------------------------------------------------------
# Column names in the input TSV
# ---------------------------------------------------------------------------
CYCLE_COL = "0"
R_COORD_COL = "R_coordinate"
HITS_INDEX_COL = "hits_index"
TAG_COL_INDEX = 4  # 0-indexed: the column name itself is the library tag DNA sequence

CYCLE_START_VALUE = 1
EXPECTED_CYCLES = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def idx_to_bbname(idx: int, lib_name: str) -> str:
    suffix = f"BB{chr(ord('A') + idx)}"
    return f"{lib_name}_{suffix}"


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(library_data: pd.DataFrame) -> tuple[bool, str | None]:
    """
    Validate the input TSV structure.
    Returns (True, library_tag) or (False, None).
    """
    library_tag = library_data.columns[TAG_COL_INDEX]
    print(f"  Library tag column: {library_tag}")

    cycle_col = library_data[CYCLE_COL]

    if cycle_col.iloc[0] != CYCLE_START_VALUE:
        print(f"  FAIL: first cycle should be {CYCLE_START_VALUE}, got {cycle_col.iloc[0]}")
        return False, None

    diffs = cycle_col.diff().dropna()
    if ((diffs != 0) & (diffs != 1)).any():
        print("  FAIL: cycle sequence is not monotonically non-decreasing")
        return False, None

    if cycle_col.max() != EXPECTED_CYCLES:
        print(f"  FAIL: expected {EXPECTED_CYCLES} cycles, got {cycle_col.max()}")
        return False, None

    if not (library_data[R_COORD_COL] == library_data[HITS_INDEX_COL]).all():
        print("  FAIL: R_coordinate and hits_index columns differ — needs investigation")
        return False, None

    max_per_cycle = library_data.groupby(CYCLE_COL)[HITS_INDEX_COL].max()
    print(f"  Building blocks per cycle: {max_per_cycle.to_dict()}")

    null_mask = library_data[library_tag].isnull()
    if null_mask.any():
        print(f"  WARNING: {null_mask.sum()} rows have null tags")
        print(library_data[null_mask][[HITS_INDEX_COL, CYCLE_COL]].to_string())
    else:
        print("  No null tags found")

    return True, library_tag


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def build_library_json(library_tag: str, lib_name: str, cfg: dict) -> dict:
    schema = {}
    schema["primer1"] = {"tag": cfg["primer1_tag"], "overhang": cfg["primer1_overhang"]}

    for i, overhang in enumerate(cfg["bb_overhangs"]):
        schema[f"bb{i + 1}"] = {
            "tag": "N" * cfg["bb_length"],
            "overhang": overhang,
            "error_correction": cfg["error_correction"],
        }

    schema["library"] = {"tag": library_tag}
    schema["umi"] = {"tag": "N" * cfg["umi_length"]}
    schema["primer2"] = {"tag": cfg["primer2_tag"]}

    bb_sets = [
        {"cycle": i + 1, "bb_set_name": idx_to_bbname(i, lib_name)}
        for i in range(len(cfg["bb_overhangs"]))
    ]

    return {
        "barcode_schema": schema,
        "bb_sets": bb_sets,
        "dna_barcode_on": idx_to_bbname(0, lib_name),
    }


def build_building_blocks(library_data: pd.DataFrame, library_tag: str, lib_name: str) -> dict[str, pd.DataFrame]:
    result = {}
    for cycle_num, cycle_data in library_data.groupby(CYCLE_COL):
        bb_name = idx_to_bbname(int(cycle_num) - 1, lib_name)
        df = cycle_data[[HITS_INDEX_COL, library_tag]].copy()
        df.columns = ["id", "tag"]
        result[bb_name] = df
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Convert vendor library TSV files to DELi JSON + CSV format.")
    parser.add_argument("--input-dir",  required=True, help="Directory containing vendor TSV files.")
    parser.add_argument("--output-dir", required=True, help="Output directory (libraries/ and building_blocks/ created inside).")
    parser.add_argument("--config",     required=True, help="Path to library_config.yml.")
    args = parser.parse_args()

    cfg = load_config(args.config)

    lib_out = os.path.join(args.output_dir, "libraries")
    bb_out  = os.path.join(args.output_dir, "building_blocks")
    os.makedirs(lib_out, exist_ok=True)
    os.makedirs(bb_out,  exist_ok=True)
    # DELi requires these directories to exist even if empty
    os.makedirs(os.path.join(args.output_dir, "reactions"),      exist_ok=True)
    os.makedirs(os.path.join(args.output_dir, "tool_compounds"), exist_ok=True)

    files = [f for f in os.listdir(args.input_dir) if os.path.isfile(os.path.join(args.input_dir, f))]
    print(f"Found {len(files)} file(s) in {args.input_dir}")

    ok, failed = 0, 0

    for filename in sorted(files):
        print(f"\n--- {filename} ---")
        path = os.path.join(args.input_dir, filename)
        lib_name = os.path.splitext(filename)[0].split("_")[-1]

        try:
            data = pd.read_csv(path, sep="\t")
            valid, library_tag = validate(data)

            if not valid:
                print("  Skipped (validation failed)")
                failed += 1
                continue

            library_json = build_library_json(library_tag, lib_name, cfg)
            building_blocks = build_building_blocks(data, library_tag, lib_name)

            json_path = os.path.join(lib_out, f"{lib_name}.json")
            with open(json_path, "w") as f:
                json.dump(library_json, f, indent=4)
            print(f"  Saved library → {json_path}")

            for bb_name, df in building_blocks.items():
                csv_path = os.path.join(bb_out, f"{bb_name}.csv")
                df.to_csv(csv_path, index=False)
            print(f"  Saved {len(building_blocks)} building block file(s) → {bb_out}/")
            ok += 1

        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            failed += 1

    print(f"\nDone: {ok} succeeded, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
