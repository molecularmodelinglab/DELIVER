# convert_hitgen_SGC

Two conversion scripts for SGC-DEL libraries.

## convert_decoding — Excel → DELi format

Converts SGC library Excel files to DELi format:
- `libraries/<lib_name>.json` — library barcode schema
- `building_blocks/<lib_name>_BBA/BBB/BBC.csv` — building block tables

No separate config file is needed — all barcode parameters are parsed from the Excel file itself.

```bash
# Via SLURM (from DELIVER root):
sbatch scripts/convert_hitgen_SGC/convert_decoding.slurm \
  --input-dir  /path/to/sgc/excel_files \
  --output-dir /path/to/deli_data
```

The SLURM script creates a local `.venv` inside `scripts/convert_hitgen_SGC/` on first run.

### Input format

One Excel file per library, named `LIBRARY-NAME BB-Codon-List.xlsx`.

#### Main sheet (`LIBRARY-NAME`)

| Cell | Content |
|------|---------|
| B16 or B14 | `SOMETHING1NNNNNNNNNNNNSOMETHING2` — `SOMETHING1` is the library tag. Row 16 used if A16 contains `"Library  ID sequencing"`, otherwise row 14. |
| B20 or B18 | Space-separated barcode layout string (see below). Row detected by A column containing `"Library Tag"`. |

**Barcode layout** — must start with `(5')`, then space-separated parts:

| Part | Description |
|------|-------------|
| `[0]` + `[1]` | Primer 1 tag (joined, no separator) |
| `[2]` | BB1: `XXXXXOVERHANG` — X count = tag length, rest = overhang |
| `[3]` | BB2: same format |
| `[4]` | BB3: same format |
| `[5]` | Library tag + `NNN...` (UMI) + primer 2. Library tag written as X placeholders (count validated against library tag length) or real sequence (validated against library tag). |

#### Cycle sheets

- `Cycle 1 BB & DNA tags`
- `Cycle 2 BB & DNA tags`
- `Cycle 3 BB & DNA tags`

Each sheet must have columns `Index` (→ `id`) and `Positive-strand Sequence` (→ `tag`).

---

## convert_smiles — enumerated .txt.gz → parquet

Converts SGC-DEL fully-enumerated structure files to sorted parquet for SMILES lookup in the pipeline.

Output columns: `compound`, `SMILES`. Sorted lexicographically by `compound`.

```bash
# Submit all SGC-DEL libraries at once (from DELIVER root):
bash scripts/convert_hitgen_SGC/run_convert_smiles.sh \
  --input-dir  /path/to/sgc/enumerated/raw_data \
  --output-dir /path/to/output/enumerated_smiles

# Or a single file:
sbatch --export=ALL,INPUT=/path/to/SGC-DEL0001_Fully_Enumerated_Structures.txt.gz,OUTPUT_DIR=/path/to/out \
       scripts/convert_hitgen_SGC/convert_smiles.slurm
```

### Input format

Tab-separated `.txt.gz` with columns `CompoundIndex` and `Smiles`.
