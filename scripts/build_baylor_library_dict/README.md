# build_baylor_library_dict

Builds a `library_dict.json` from Baylor OpenDEL_v3 library JSON files for use in the DELIVER pipeline.

## Usage

```bash
sbatch scripts/build_baylor_library_dict/build_baylor_library_dict.slurm \
  --input-dir /proj/tropshalab/shared/Baylor/libraries/OpenDEL_v3 \
  --output    /proj/tropshalab/shared/Baylor/utilities/libraries/OpenDEL_v3/library_dict.json
```

## Output

```json
{
  "qDOS11": {"A": 46, "B": 1272, "C": 1327},
  ...
}
```

Cycles `1`, `2`, `3` in the source JSONs are mapped to `A`, `B`, `C`.
