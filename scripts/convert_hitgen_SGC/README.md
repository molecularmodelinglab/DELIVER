# convert_hitgen_SGC

Two conversion scripts for SGC-DEL libraries.

## convert_decoding — TXT + Excel → DELi format

Converts SGC-DEL library files to DELi format:
- `libraries/<lib_name>.json` — library barcode schema
- `building_blocks/<lib_name>_BBA/BBB/BBC.csv` — building block tables

Building block data comes from TXT files; barcode schema (primers, overhangs, UMI) comes from matching Excel files.

```bash
# Via SLURM (from DELIVER root):
sbatch scripts/convert_hitgen_SGC/convert_decoding.slurm \
  --input-dir  /path/to/sgc/txt_files \
  --excel-dir  /path/to/sgc/excel_files \
  --output-dir /path/to/deli_data
```

The SLURM script creates a local `.venv` inside `scripts/convert_hitgen_SGC/` on first run.

Libraries are matched by name: `similarity_NReagent_SGC-DEL0001.txt` pairs with `SGC-DEL0001 BB-Codon-List.xlsx`.

### TXT input (building block data)

One tab-separated `.txt` file per library containing all 3 cycles. Required columns:

| Column | Content |
|--------|---------|
| `hits_index` | Building block ID (integer) |
| `0` | Cycle number (1, 2, or 3) |
| (column 2) | SMILES — skipped |
| (column 3, name = library tag) | BB DNA tag |

### Excel input (barcode schema)

One `.xlsx` file per library, named `LIBRARY-NAME BB-Codon-List.xlsx`.

Main sheet (`LIBRARY-NAME`):

| Cell | Content |
|------|---------|
| B16 or B14 | Library tag sequence (`SOMETHING1NNNNN...SOMETHING2` — everything before the first N is the library tag). Row 16 used if A16 contains `"Library  ID sequencing"`, otherwise row 14. |
| B20, B18, or B19 | Space-separated barcode layout starting with `(5')`. Row detected by A column containing `"Library Tag"`. |

**Barcode layout** parts (space-separated after `(5')`):

| Part | Description |
|------|-------------|
| `[0]` + `[1]` | Primer 1 tag (joined) |
| `[2]–[4]` | BB1–BB3: `XXXXXOVERHANG` — X count = tag length, rest = overhang |
| `[5]` | Library tag + `NNN...` (UMI) + primer 2 |

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
