"""
Convert SGC-DEL library files to DELi library JSON + building block CSV format.

Building block data comes from TXT files (tab-separated):
  similarity_NReagent_SGC-DEL0001.txt

Barcode schema (primers, overhangs, UMI) is parsed from matching Excel files:
  SGC-DEL0001 BB-Codon-List.xlsx

Each matched pair is converted to:
  <output-dir>/libraries/<lib_name>.json
  <output-dir>/building_blocks/<lib_name>_BBA.csv
  <output-dir>/building_blocks/<lib_name>_BBB.csv
  <output-dir>/building_blocks/<lib_name>_BBC.csv

Usage:
  python convert_decoding.py --input-dir DIR --excel-dir DIR --output-dir DIR
"""

import argparse
import json
import os
import re
import sys

import pandas as pd


# ---------------------------------------------------------------------------
# TXT column layout
# ---------------------------------------------------------------------------
CYCLE_COL      = "0"
HITS_INDEX_COL = "hits_index"
TAG_COL_INDEX  = 4   # column name = library tag; values = BB tags

CYCLE_START_VALUE = 1
EXPECTED_CYCLES   = 3

# ---------------------------------------------------------------------------
# Excel layout (schema parsing — unchanged from original)
# ---------------------------------------------------------------------------
COL_LABEL = 0
COL_VALUE = 1

LIBRARY_ID_ROW_PRIMARY   = 15
LIBRARY_ID_ROW_SECONDARY = 13
LIBRARY_ID_LABEL         = "Library  ID sequencing"

LAYOUT_CANDIDATE_ROWS = [17, 19, 20]
LAYOUT_LABEL          = "Library Tag"
LAYOUT_PREFIX         = "(5')"

DEFAULT_ERROR_CORRECTION = "levenshtein_dist:1,asymmetrical"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def idx_to_bbname(idx: int, lib_name: str) -> str:
    return f"{lib_name}_BB{chr(ord('A') + idx)}"


def _cell(df: pd.DataFrame, row: int, col: int) -> str:
    val = df.iloc[row, col]
    return str(val).strip() if pd.notna(val) else ""


def txt_lib_name(filename: str) -> str:
    """'similarity_NReagent_SGC-DEL0001.txt' → 'SGC-DEL0001'"""
    return os.path.splitext(filename)[0].split("_")[-1]


def excel_lib_name(filename: str) -> str | None:
    """'SGC-DEL0001 BB-Codon-List.xlsx' → 'SGC-DEL0001'"""
    stem = os.path.splitext(filename)[0]
    if " BB-Codon-List" not in stem:
        return None
    return stem.split(" BB-Codon-List")[0]


def index_excel_files(excel_dir: str) -> dict[str, str]:
    """Return {lib_name: full_path} for all Excel files in excel_dir."""
    index = {}
    for f in os.listdir(excel_dir):
        if not f.lower().endswith((".xlsx", ".xls")):
            continue
        name = excel_lib_name(f)
        if name:
            index[name] = os.path.join(excel_dir, f)
    return index


# ---------------------------------------------------------------------------
# Schema parsing from Excel
# ---------------------------------------------------------------------------

def get_library_tag(df_main: pd.DataFrame) -> str:
    label_primary = _cell(df_main, LIBRARY_ID_ROW_PRIMARY, COL_LABEL)
    row = LIBRARY_ID_ROW_PRIMARY if LIBRARY_ID_LABEL in label_primary else LIBRARY_ID_ROW_SECONDARY
    value = _cell(df_main, row, COL_VALUE)
    m = re.match(r'^([^N]+)', value)
    if not m:
        raise ValueError(f"Cannot extract library tag from Excel row {row + 1}: {value!r}")
    return m.group(1)


def get_schema_params(df_main: pd.DataFrame, library_tag: str) -> dict:
    for row in LAYOUT_CANDIDATE_ROWS:
        if LAYOUT_LABEL in _cell(df_main, row, COL_LABEL):
            return _parse_barcode_layout(_cell(df_main, row, COL_VALUE), library_tag)
    raise ValueError(f"Could not find '{LAYOUT_LABEL}' in Excel rows {[r+1 for r in LAYOUT_CANDIDATE_ROWS]}")


def _parse_barcode_layout(value: str, library_tag: str) -> dict:
    if not value.startswith(LAYOUT_PREFIX):
        raise ValueError(f"Barcode layout does not start with {LAYOUT_PREFIX!r}: {value[:40]!r}")
    parts = value[len(LAYOUT_PREFIX):].lstrip().split()
    if len(parts) < 6:
        raise ValueError(f"Barcode layout has {len(parts)} parts, expected at least 6")

    primer1_tag = parts[0] + parts[1]
    bb_lengths, bb_overhangs = [], []
    for i in range(2, 5):
        m = re.match(r'^(X+)(.+)$', parts[i], re.IGNORECASE)
        if not m:
            raise ValueError(f"Barcode layout part [{i}] does not match XXXOVERHANG: {parts[i]!r}")
        bb_lengths.append(len(m.group(1)))
        bb_overhangs.append(m.group(2))

    tail = _strip_library_tag(parts[5], library_tag)
    n_match = re.match(r'^(N+)(.+)$', tail, re.IGNORECASE)
    if not n_match:
        raise ValueError(f"Expected NNN...PRIMER2 after library tag, got: {tail!r}")

    return {
        "primer1_tag":  primer1_tag,
        "bb_lengths":   bb_lengths,
        "bb_overhangs": bb_overhangs,
        "umi_length":   len(n_match.group(1)),
        "primer2_tag":  n_match.group(2),
    }


def _strip_library_tag(part: str, library_tag: str) -> str:
    if re.match(r'^X', part, re.IGNORECASE):
        x_count = len(re.match(r'^(X+)', part, re.IGNORECASE).group(1))
        if x_count != len(library_tag):
            print(f"  WARNING: layout has {x_count} X's but library tag length is {len(library_tag)}")
        return part[x_count:]
    else:
        real_tag = part[:len(library_tag)]
        if real_tag.upper() != library_tag.upper():
            print(f"  WARNING: layout tag {real_tag!r} does not match library tag {library_tag!r}")
        return part[len(library_tag):]


# ---------------------------------------------------------------------------
# BB data parsing from TXT
# ---------------------------------------------------------------------------

def validate_txt(data: pd.DataFrame) -> tuple[bool, str | None]:
    library_tag = data.columns[TAG_COL_INDEX]
    print(f"  Library tag column (TXT): {library_tag}")

    cycle_col = data[CYCLE_COL]
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

    counts = data.groupby(CYCLE_COL)[HITS_INDEX_COL].count()
    print(f"  Building blocks per cycle: {counts.to_dict()}")

    null_mask = data[library_tag].isnull()
    if null_mask.any():
        print(f"  WARNING: {null_mask.sum()} rows have null tags")

    return True, library_tag


def build_building_blocks(
    data: pd.DataFrame,
    library_tag: str,
    lib_name: str,
    overhangs: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    result = {}
    for cycle_num, cycle_data in data.groupby(CYCLE_COL):
        bb_name = idx_to_bbname(int(cycle_num) - 1, lib_name)
        df = cycle_data[[HITS_INDEX_COL, library_tag]].copy()
        df.columns = ["id", "tag"]
        if overhangs is not None:
            df["tag"] = df["tag"] + overhangs[int(cycle_num) - 1]
        df = df.sort_values("id").reset_index(drop=True)
        result[bb_name] = df
    return result


# ---------------------------------------------------------------------------
# Library JSON
# ---------------------------------------------------------------------------

def build_library_json(
    library_tag: str, lib_name: str, schema: dict, error_correction: str, with_overhang: bool
) -> dict:
    out = {}
    out["primer1"] = {"tag": schema["primer1_tag"], "overhang": ""}

    for i, (length, overhang) in enumerate(zip(schema["bb_lengths"], schema["bb_overhangs"])):
        entry = {"tag": "N" * length, "error_correction": error_correction}
        if not with_overhang:
            entry["overhang"] = overhang
        out[f"bb{i + 1}"] = entry

    out["library"] = {"tag": library_tag}
    out["umi"]     = {"tag": "N" * schema["umi_length"]}
    out["primer2"] = {"tag": schema["primer2_tag"]}

    bb_sets = [
        {"cycle": i + 1, "bb_set_name": idx_to_bbname(i, lib_name)}
        for i in range(len(schema["bb_overhangs"]))
    ]
    return {"barcode_schema": out, "bb_sets": bb_sets, "dna_barcode_on": idx_to_bbname(0, lib_name)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir",       required=True, help="Directory containing SGC-DEL TXT files.")
    parser.add_argument("--excel-dir",       required=True, help="Directory containing matching Excel files (for barcode schema).")
    parser.add_argument("--output-dir",      required=True, help="Output directory.")
    parser.add_argument("--error-correction", default=DEFAULT_ERROR_CORRECTION,
                        help=f"Error correction for BB barcodes (default: {DEFAULT_ERROR_CORRECTION}).")
    parser.add_argument("--with-overhang",   action="store_true",
                        help="Append cycle overhang to each BB tag; omit overhang from library JSON schema.")
    args = parser.parse_args()

    lib_out = os.path.join(args.output_dir, "libraries")
    bb_out  = os.path.join(args.output_dir, "building_blocks")
    os.makedirs(lib_out, exist_ok=True)
    os.makedirs(bb_out,  exist_ok=True)
    os.makedirs(os.path.join(args.output_dir, "reactions"),      exist_ok=True)
    os.makedirs(os.path.join(args.output_dir, "tool_compounds"), exist_ok=True)

    excel_index = index_excel_files(args.excel_dir)
    print(f"Found {len(excel_index)} Excel file(s) in {args.excel_dir}")

    txt_files = [
        f for f in os.listdir(args.input_dir)
        if os.path.isfile(os.path.join(args.input_dir, f)) and f.lower().endswith(".txt")
    ]
    print(f"Found {len(txt_files)} TXT file(s) in {args.input_dir}")

    ok, failed = 0, 0

    for filename in sorted(txt_files):
        print(f"\n--- {filename} ---")
        lib_name = txt_lib_name(filename)
        print(f"  Library name: {lib_name}")

        try:
            excel_path = excel_index.get(lib_name)
            if not excel_path:
                raise FileNotFoundError(f"No matching Excel file found for library '{lib_name}' in {args.excel_dir}")
            print(f"  Excel: {os.path.basename(excel_path)}")

            df_main     = pd.read_excel(excel_path, sheet_name=lib_name, header=None)
            library_tag = get_library_tag(df_main)
            schema      = get_schema_params(df_main, library_tag)
            print(f"  Library tag:  {library_tag}")
            print(f"  BB lengths:   {schema['bb_lengths']}")
            print(f"  BB overhangs: {schema['bb_overhangs']}")
            print(f"  UMI length:   {schema['umi_length']}")

            data = pd.read_csv(os.path.join(args.input_dir, filename), sep="\t")
            valid, txt_tag = validate_txt(data)
            if not valid:
                print("  Skipped (TXT validation failed)")
                failed += 1
                continue

            if txt_tag != library_tag:
                print(f"  WARNING: TXT library tag ({txt_tag!r}) differs from Excel library tag ({library_tag!r})")

            overhangs       = schema["bb_overhangs"] if args.with_overhang else None
            library_json    = build_library_json(library_tag, lib_name, schema, args.error_correction, args.with_overhang)
            building_blocks = build_building_blocks(data, txt_tag, lib_name, overhangs=overhangs)

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
