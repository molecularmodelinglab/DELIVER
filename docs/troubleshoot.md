# Troubleshooting — Longleaf (SLURM)

## Log files

Each run produces two log files in `--log-dir`:

| File | Contains |
|------|----------|
| `launcher_<timestamp>.log` | Nextflow progress — submitted/completed process names, final status |
| `nextflow_<timestamp>.log` | Detailed internals — task hashes, work directories, exit codes, retry attempts |

---

## Finding the failed task

### Step 1 — find the process name and hash in the nextflow log

Search for `FAILED` or `terminated` in the latest nextflow log:

```
Jun-29 12:05:40.160 [Task monitor] ... Task completed > TaskHandler[jobId: 57005643; id: 5; name: POSTPROCESS:JOIN (join); status: COMPLETED; exit: -; ...
Jun-29 12:05:40.185 [TaskFinalizer-5] ... Process `POSTPROCESS:JOIN (join)` terminated for an unknown reason -- Likely it has been terminated by the external system
Jun-29 12:05:40.213 [TaskFinalizer-5] ... [66/a45068] NOTE: Process `POSTPROCESS:JOIN (join)` terminated for an unknown reason -- Likely it has been terminated by the external system -- Execution is retried (1)
```

The short hash in brackets (`[66/a45068]`) also appears in the submitted line:

```
Jun-29 11:47:35.621 [Task submitter] INFO  nextflow.Session - [66/a45068] Submitted process > POSTPROCESS:JOIN (join)
```

### Step 2 — find the full work directory path

In the same log, the full `workDir` appears in the `TaskHandler` line:

```
~> TaskHandler[jobId: 57005643; id: 5; name: POSTPROCESS:JOIN (join); status: RUNNING; exit: -; error: -;
   workDir: /work/users/v/a/valk/practice_update/work/66/a4506897c05160a9c59d6ae482d0e3 ...]
```

Alternatively, use the short hash directly:

```bash
ls <work-dir>/66/a4506897c05160a9c59d6ae482d0e3/
```

Or find it with:

```bash
ls <work-dir>/66/
```

### Step 3 — read the error

```bash
cat <work-dir>/66/a4506897c05160a9c59d6ae482d0e3/.command.err
cat <work-dir>/66/a4506897c05160a9c59d6ae482d0e3/.command.log
```

---

## Common failures

### Out of memory (OOM)

**Symptom in nextflow log:**
```
Process `POSTPROCESS:JOIN (join)` terminated for an unknown reason -- Likely it has been terminated by the external system
```

**Symptom in `.command.err`:**
```
[2026-06-29T11:47:51.540] error: Detected 9 oom_kill events in StepId=57005643.batch. Some of the step tasks have been OOM Killed.
```

Note: `.command.err` may be empty even for OOM kills — the SLURM error appears only in `.command.log`.

**Fix:** increase memory for the failing process in [pipeline/nextflow.config](../pipeline/nextflow.config) under the `longleaf` profile, then resubmit with `--resume`.

### Process failed with a Python error

**Symptom in `.command.err`:**
```
Error: compound_id is not unique (12045 duplicate rows)
```

Read `.command.sh` to see the exact command that was run, then reproduce it locally to debug.

### Run was interrupted / SLURM job timed out

The launcher job itself has a 6-hour wall time. If the pipeline is still running when it expires, resubmit with `--resume`.

---

## Resuming after failure

Add `--resume` to the original `sbatch` command:

```bash
sbatch /proj/tropshalab/shared/deliver/DELIVER/submit.slurm \
  --params-file <params-file> \
  --work-dir    <same-work-dir-as-before> \
  --cache-dir   <same-cache-dir-as-before> \
  --log-dir     <log-dir> \
  --resume
```

`--work-dir` and `--cache-dir` must match the original run exactly — Nextflow uses them to find which steps already completed.

---

## Disk space

Use a **per-run** work directory so you can delete one run's intermediate files without affecting others:

```
/work/users/v/a/valk/<run-name>/work
/work/users/v/a/valk/<run-name>/.nextflow
/work/users/v/a/valk/<run-name>/logs
```

To free space after a successful run:

```bash
rm -rf /work/users/v/a/valk/<run-name>/work
```

The `.nextflow` cache can also be deleted if you don't need to resume that run. Final results in `out_dir` are unaffected.

---

## Checking SLURM job status

```bash
squeue -u $USER        # jobs currently queued or running
sacct -j <jobId>       # history and exit codes for a specific job
```
