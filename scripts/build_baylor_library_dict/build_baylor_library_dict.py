"""Build a DELIVER library_dict.json from Baylor OpenDEL_v3 library JSON files."""

import argparse
import json
from pathlib import Path

CYCLE_MAP = {"1": "A", "2": "B", "3": "C"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir",  required=True, help="Directory containing library JSON files.")
    parser.add_argument("--output",     required=True, help="Output library_dict.json path.")
    args = parser.parse_args()

    result = {}
    for path in sorted(Path(args.input_dir).glob("*.json")):
        data = json.loads(path.read_text())
        lib_id = data["library"]
        result[lib_id] = {CYCLE_MAP[k]: len(data[k]) for k in sorted(data) if k in CYCLE_MAP}

    Path(args.output).write_text(json.dumps(result, indent=2))
    print(f"Wrote {len(result)} libraries to {args.output}")


if __name__ == "__main__":
    main()
