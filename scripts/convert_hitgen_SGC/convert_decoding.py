"""
Convert SGC library Excel files to DELi library JSON + building block CSV format.

Each Excel file ("LIBRARY-NAME BB-Codon-List.xlsx") is converted to:
  <output-dir>/libraries/<lib_name>.json
  <output-dir>/building_blocks/<lib_name>_BBA.csv
  <output-dir>/building_blocks/<lib_name>_BBB.csv
  <output-dir>/building_blocks/<lib_name>_BBC.csv

Barcode schema is parsed directly from the Excel file (no separate config needed).

Usage:
  python convert_hitgen_SGC.py --input-dir DIR --output-dir DIR
"""

import argparse
import json
import os
import re
import sys

import pandas as pd


# ---------------------------------------------------------------------------
# Excel layout
# All row/column indices are 0-based; comments note the 1-based Excel address.
# ---------------------------------------------------------------------------
COL_LABEL = 0   # column A — row labels / descriptions
COL_VALUE = 1   # column B — the actual data values

# Library tag cell: check the primary row first; fall back to the secondary row.
LIBRARY_ID_ROW_PRIMARY   = 15   # Excel row 16
LIBRARY_ID_ROW_SECONDARY = 13   # Excel row 14
LIBRARY_ID_LABEL         = "Library  ID sequencing"   # expected substring in col A

# Barcode layout row: search all candidate rows for LAYOUT_LABEL in col A.
LAYOUT_CANDIDATE_ROWS = [17, 19, 20]   # Excel rows 18, 20, 21
LAYOUT_LABEL          = "Library Tag"  # expected substring in col A
LAYOUT_PREFIX         = "(5')"         # required prefix of the layout string

# ---------------------------------------------------------------------------
# Building block cycle sheets and output config
# ---------------------------------------------------------------------------
CYCLE_SHEETS = [
    "Cycle 1 BB & DNA tags",
    "Cycle 2 BB & DNA tags",
    "Cycle 3 BB & DNA tags",
]
BB_ID_COLUMN  = "Index"
BB_TAG_COLUMN = "Positive-strand Sequence"

DEFAULT_ERROR_CORRECTION = "levenshtein_dist:1,asymmetrical"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cell(df: pd.DataFrame, row: int, col: int) -> str:
    """Return a cell value as a stripped string, or '' if null."""
    val = df.iloc[row, col]
    return str(val).strip() if pd.notna(val) else ""


def idx_to_bbname(idx: int, lib_name: str) -> str:
    return f"{lib_name}_BB{chr(ord('A') + idx)}"


def parse_library_name(filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    if " BB-Codon-List" not in stem:
        raise ValueError(
            f"Filename does not match expected 'LIBRARY-NAME BB-Codon-List.xlsx': {filename!r}"
        )
    return stem.split(" BB-Codon-List")[0]


# ---------------------------------------------------------------------------
# Parsing the main sheet
# ---------------------------------------------------------------------------

def get_library_tag(df_main: pd.DataFrame) -> str:
    """
    Read the library tag from column B.
    Uses LIBRARY_ID_ROW_PRIMARY if its label cell contains LIBRARY_ID_LABEL,
    otherwise falls back to LIBRARY_ID_ROW_SECONDARY.
    The tag is the non-N prefix of the cell value (e.g. 'ACGT' from 'ACGTNNNNN...').
    """
    label_primary = _cell(df_main, LIBRARY_ID_ROW_PRIMARY, COL_LABEL)
    if LIBRARY_ID_LABEL in label_primary:
        row = LIBRARY_ID_ROW_PRIMARY
    else:
        row = LIBRARY_ID_ROW_SECONDARY
    print(f"  Library tag: reading from row {row + 1}")

    value = _cell(df_main, row, COL_VALUE)
    m = re.match(r'^([^N]+)', value)
    if not m:
        raise ValueError(
            f"Cannot extract library tag from row {row + 1} "
            f"(expected non-N prefix, got): {value!r}"
        )
    return m.group(1)


def get_schema_params(df_main: pd.DataFrame, library_tag: str) -> dict:
    """
    Find the barcode layout row and parse it.
    Scans LAYOUT_CANDIDATE_ROWS for the first row whose col A contains LAYOUT_LABEL.
    """
    for row in LAYOUT_CANDIDATE_ROWS:
        if LAYOUT_LABEL in _cell(df_main, row, COL_LABEL):
            print(f"  Barcode layout: found in row {row + 1}")
            return _parse_barcode_layout(_cell(df_main, row, COL_VALUE), library_tag)

    candidate_excel_rows = [r + 1 for r in LAYOUT_CANDIDATE_ROWS]
    raise ValueError(
        f"Could not find barcode layout: expected '{LAYOUT_LABEL}' in column A "
        f"of rows {candidate_excel_rows}"
    )


def _parse_barcode_layout(value: str, library_tag: str) -> dict:
    """
    Parse the space-separated barcode layout string.

    Must start with LAYOUT_PREFIX (e.g. "(5')"). Parts after stripping the prefix:
      [0] + [1]  primer1 tag (two parts joined together)
      [2]        bb1: XXXXXOVERHANG — X count = bb tag length, rest = overhang
      [3]        bb2: same format
      [4]        bb3: same format
      [5]        library tag position + NNN (UMI) + primer2:
                   • as X placeholders → X count validated against len(library_tag)
                   • as real sequence  → sequence validated against library_tag
    """
    if not value.startswith(LAYOUT_PREFIX):
        raise ValueError(
            f"Barcode layout does not start with {LAYOUT_PREFIX!r}: {value[:40]!r}"
        )
    body = value[len(LAYOUT_PREFIX):].lstrip()

    parts = body.split()
    if len(parts) < 6:
        raise ValueError(
            f"Barcode layout has {len(parts)} space-separated parts "
            f"after {LAYOUT_PREFIX!r}, expected at least 6"
        )

    primer1_tag = parts[0] + parts[1]

    bb_lengths   = []
    bb_overhangs = []
    for i in range(2, 5):
        m = re.match(r'^(X+)(.+)$', parts[i], re.IGNORECASE)
        if not m:
            raise ValueError(
                f"Barcode layout part [{i}] does not match XXXOVERHANG format: {parts[i]!r}"
            )
        bb_lengths.append(len(m.group(1)))
        bb_overhangs.append(m.group(2))

    tail = _strip_library_tag_from_layout(parts[5], library_tag)

    n_match = re.match(r'^(N+)(.+)$', tail, re.IGNORECASE)
    if not n_match:
        raise ValueError(
            f"Barcode layout part [5]: expected NNN...PRIMER2 after library tag, got: {tail!r}"
        )
    umi_length  = len(n_match.group(1))
    primer2_tag = n_match.group(2)

    return {
        "primer1_tag":  primer1_tag,
        "bb_lengths":   bb_lengths,
        "bb_overhangs": bb_overhangs,
        "umi_length":   umi_length,
        "primer2_tag":  primer2_tag,
    }


def _strip_library_tag_from_layout(part: str, library_tag: str) -> str:
    """
    Remove the library tag position from the start of part and return the remainder.
    The library tag position is written either as X placeholders or as the real sequence.
    Prints a warning if the placeholder length or real sequence does not match library_tag.
    """
    if re.match(r'^X', part, re.IGNORECASE):
        x_count = len(re.match(r'^(X+)', part, re.IGNORECASE).group(1))
        if x_count != len(library_tag):
            print(
                f"  WARNING: library placeholder in barcode layout is {x_count} X's "
                f"but library tag length is {len(library_tag)}"
            )
        return part[x_count:]
    else:
        real_tag = part[:len(library_tag)]
        if real_tag.upper() != library_tag.upper():
            print(
                f"  WARNING: library tag in barcode layout ({real_tag!r}) "
                f"does not match library tag ({library_tag!r})"
            )
        return part[len(library_tag):]


# ---------------------------------------------------------------------------
# Building the output
# ---------------------------------------------------------------------------

def build_library_json(library_tag: str, lib_name: str, schema_params: dict, error_correction: str) -> dict:
    schema = {}
    schema["primer1"] = {"tag": schema_params["primer1_tag"], "overhang": ""}

    for i, (length, overhang) in enumerate(zip(schema_params["bb_lengths"], schema_params["bb_overhangs"])):
        schema[f"bb{i + 1}"] = {
            "tag": "N" * length,
            "overhang": overhang,
            "error_correction": error_correction,
        }

    schema["library"] = {"tag": library_tag}
    schema["umi"]     = {"tag": "N" * schema_params["umi_length"]}
    schema["primer2"] = {"tag": schema_params["primer2_tag"]}

    bb_sets = [
        {"cycle": i + 1, "bb_set_name": idx_to_bbname(i, lib_name)}
        for i in range(len(schema_params["bb_overhangs"]))
    ]

    return {
        "barcode_schema": schema,
        "bb_sets":        bb_sets,
        "dna_barcode_on": idx_to_bbname(0, lib_name),
    }


def build_building_blocks(path: str, lib_name: str) -> dict[str, pd.DataFrame]:
    result = {}
    for i, sheet_name in enumerate(CYCLE_SHEETS):
        df = pd.read_excel(path, sheet_name=sheet_name)
        for col in (BB_ID_COLUMN, BB_TAG_COLUMN):
            if col not in df.columns:
                raise ValueError(f"Sheet '{sheet_name}' missing column '{col}'")
        out = df[[BB_ID_COLUMN, BB_TAG_COLUMN]].copy()
        out.columns = ["id", "tag"]
        result[idx_to_bbname(i, lib_name)] = out
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert SGC library Excel files to DELi JSON + CSV format."
    )
    parser.add_argument("--input-dir",  required=True, help="Directory containing SGC Excel files.")
    parser.add_argument("--output-dir", required=True, help="Output directory.")
    parser.add_argument(
        "--error-correction",
        default=DEFAULT_ERROR_CORRECTION,
        help=f"Error correction string for BB barcodes (default: {DEFAULT_ERROR_CORRECTION}).",
    )
    args = parser.parse_args()

    lib_out = os.path.join(args.output_dir, "libraries")
    bb_out  = os.path.join(args.output_dir, "building_blocks")
    os.makedirs(lib_out, exist_ok=True)
    os.makedirs(bb_out,  exist_ok=True)
    os.makedirs(os.path.join(args.output_dir, "reactions"),      exist_ok=True)
    os.makedirs(os.path.join(args.output_dir, "tool_compounds"), exist_ok=True)

    files = [
        f for f in os.listdir(args.input_dir)
        if os.path.isfile(os.path.join(args.input_dir, f)) and f.lower().endswith((".xlsx", ".xls"))
    ]
    print(f"Found {len(files)} Excel file(s) in {args.input_dir}")

    ok, failed = 0, 0

    for filename in sorted(files):
        print(f"\n--- {filename} ---")
        path = os.path.join(args.input_dir, filename)

        try:
            lib_name = parse_library_name(filename)
            print(f"  Library name: {lib_name}")

            df_main = pd.read_excel(path, sheet_name=lib_name, header=None)
            library_tag   = get_library_tag(df_main)
            schema_params = get_schema_params(df_main, library_tag)

            print(f"  Library tag:  {library_tag}")
            print(f"  Primer1:      {schema_params['primer1_tag']}")
            print(f"  BB lengths:   {schema_params['bb_lengths']}")
            print(f"  BB overhangs: {schema_params['bb_overhangs']}")
            print(f"  UMI length:   {schema_params['umi_length']}")
            print(f"  Primer2:      {schema_params['primer2_tag']}")

            library_json    = build_library_json(library_tag, lib_name, schema_params, args.error_correction)
            building_blocks = build_building_blocks(path, lib_name)

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
