# convert_hitgen

Converts Hitgen library TSV files to DELi format:
- `libraries/<lib_name>.json` — library barcode schema
- `building_blocks/<lib_name>_BBA/BBB/BBC.csv` — building block tables

## Setup

Copy the example config and fill in your values:

```bash
cp scripts/convert_hitgen/library_config_example.yml scripts/convert_hitgen/library_config.yml
# edit library_config.yml
```

`library_config.yml` is gitignored and will not be committed.

## Run

```bash
# Via SLURM (from DELIVER root):
sbatch scripts/convert_hitgen/convert_hitgen.slurm \
  --input-dir  /path/to/hitgen/tsv_files \
  --output-dir /path/to/deli_data \
  --config     scripts/convert_hitgen/library_config.yml
```

The SLURM script creates a local `.venv` inside `scripts/convert_hitgen/` on first run.

## Input format

Tab-separated files from Hitgen, one per library. Example (with fake tags):

```
R_coordinate	hits_index	0	lib_id	AAAAAAAAAAAAA	L00	structure	deletion	similarity_coordinate
1	1	1	[H]	AAAAAAAAAAA	000	[H]		1
2	2	1	[H]	CCCCCCCCCCC	001	[H]		2
1	1	2	[H]	GGGGGGGGGGG	000	[H]		1
```

Column layout (0-indexed):

| Index | Name | Description |
|-------|------|-------------|
| 0 | `R_coordinate` | Building block index (must equal `hits_index`) |
| 1 | `hits_index` | Building block index — used as `id` in output CSV |
| 2 | `0` | Cycle number (1, 2, 3) |
| 3 | `lib_id` | Library identifier (not used) |
| **4** | **`AAACCCGGGTTT`** | **The column name itself is the library tag DNA sequence** |

> **Note:** column 4 is 0-indexed (i.e. the 5th column). Its name is not a label — it is the actual DNA barcode tag for this library, and will be written into the library JSON as `"library": {"tag": "..."}`.

Library name is derived from the filename: `something_L01.tsv` → `L01`.

## Config

See `library_config_example.yml` for all options (primer sequences, overhangs, barcode lengths, error correction).
