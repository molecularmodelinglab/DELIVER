"""
Parse a Nextflow log file and report SLURM job stats.

Usage (from DELIVER root):
    python scripts/slurm_analysis/nf_job_summary.py \\
        [--log .nextflow.log] \\
        [--seff] \\
        [--config pipeline/nextflow.config] \\
        [--output jobs.csv]

Output:
  Per-job table printed to stdout (saved to --output if given).
  Aggregated summary by process type always printed at the end.
  With --config, summary includes requested cpus/memory/time from the
  longleaf profile in nextflow.config.
"""

import argparse
import csv
import math
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ── Regex patterns ────────────────────────────────────────────────────────────

RE_SUBMITTED = re.compile(r"\[SLURM\] submitted process (.+?) > jobId: (\d+)")
RE_COMPLETED = re.compile(r"Task completed > TaskHandler\[(.+?)\]")
RE_KV        = re.compile(r"(\w+): ([^;]+)")
RE_STARTED   = re.compile(r"\bstarted: (\d+|-)")
RE_EXITED    = re.compile(r"\bexited: ([^;]+)")

RE_SEFF_WALL    = re.compile(r"Job Wall-clock time:\s+(\S+)")
RE_SEFF_CPU_EFF = re.compile(r"CPU Efficiency:\s+(\S+)")
RE_SEFF_MEM_USE = re.compile(r"Memory Utilized:\s+(.+)")
RE_SEFF_MEM_EFF = re.compile(r"Memory Efficiency:\s+(\S+)")
RE_SEFF_CORES   = re.compile(r"Cores per node:\s+(\d+)")
RE_SEFF_MEM_REQ = re.compile(r"Memory Efficiency:.*of\s+([\d.]+\s*\S+)")


# ── Pipeline timing ──────────────────────────────────────────────────────────

RE_LOG_TS     = re.compile(r"^(\w{3}-\d{2} \d{2}:\d{2}:\d{2})\.")
RE_SUBMIT_LOG = re.compile(r"^(\w{3}-\d{2} \d{2}:\d{2}:\d{2})\..*submitted process .+jobId: (\d+)")


def parse_pipeline_times(
    log_path: str, jobs: list[dict]
) -> tuple[datetime | None, datetime | None, float | None]:
    """
    Return (start_utc, end_utc, total_minutes).

    start_utc  — first line of the log converted to UTC
                 (UTC offset inferred from epoch-ms of the first submitted job)
    end_utc    — max exited timestamp across all completed jobs
    """
    first_log_str    = None   # "Apr-07 11:43:26"
    first_submit_str = None   # same format, time of first SLURM submission
    first_job_id     = None

    with open(log_path) as f:
        for line in f:
            if first_log_str is None:
                m = RE_LOG_TS.match(line)
                if m:
                    first_log_str = m.group(1)
            if first_submit_str is None:
                m = RE_SUBMIT_LOG.match(line)
                if m:
                    first_submit_str = m.group(1)
                    first_job_id     = m.group(2)
            if first_log_str and first_submit_str:
                break

    end_dt = max((j["exited"] for j in jobs if "exited" in j), default=None)
    if not end_dt or not first_log_str or not first_submit_str:
        return None, end_dt, None

    year = end_dt.year

    # Infer UTC offset (whole hours) from the first submitted job's epoch-ms.
    # Parse the submission log timestamp as if it were UTC ("pseudo-UTC"),
    # compare to the job's actual started time (true UTC), round to nearest hour.
    job_lookup = {j["job_id"]: j for j in jobs}
    utc_offset_h = None
    if first_job_id and first_job_id in job_lookup:
        j = job_lookup[first_job_id]
        if "started" in j:
            pseudo_utc = datetime.strptime(
                f"{year}-{first_submit_str}", "%Y-%b-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            diff_h = (j["started"] - pseudo_utc).total_seconds() / 3600
            utc_offset_h = round(diff_h)   # e.g. 4 for EDT (UTC = local + 4h)

    if utc_offset_h is None:
        return None, end_dt, None

    first_log_naive = datetime.strptime(
        f"{year}-{first_log_str}", "%Y-%b-%d %H:%M:%S"
    )
    # Convert local → UTC:  UTC = local + utc_offset_h
    start_dt = first_log_naive.replace(tzinfo=timezone.utc) + timedelta(hours=utc_offset_h)

    total_min = round((end_dt - start_dt).total_seconds() / 60, 1)
    return start_dt, end_dt, total_min


# ── Log parsing ───────────────────────────────────────────────────────────────

def parse_log(path: str) -> list[dict]:
    """Return one dict per submitted job."""
    jobs: dict[str, dict] = {}

    with open(path) as f:
        for line in f:
            m = RE_SUBMITTED.search(line)
            if m:
                process, job_id = m.group(1).strip(), m.group(2)
                jobs[job_id] = {"job_id": job_id, "process": process}
                continue

            m = RE_COMPLETED.search(line)
            if m:
                block  = m.group(1)
                kv     = dict(RE_KV.findall(block))
                job_id = kv.get("jobId", "").strip()
                if job_id not in jobs:
                    continue
                rec = jobs[job_id]
                rec["status"]    = kv.get("status", "").strip()
                rec["exit_code"] = kv.get("exit", "").strip()

                ms = RE_STARTED.search(block)
                ex = RE_EXITED.search(block)
                if ms and ms.group(1) != "-":
                    rec["started"] = datetime.fromtimestamp(
                        int(ms.group(1)) / 1000, tz=timezone.utc
                    )
                if ex and ex.group(1).strip() != "-":
                    rec["exited"] = datetime.fromisoformat(
                        ex.group(1).strip().rstrip("Z") + "+00:00"
                    )
                if "started" in rec and "exited" in rec:
                    delta = rec["exited"] - rec["started"]
                    dur   = delta.total_seconds() / 60
                    rec["duration_min"] = round(dur, 2) if dur >= 0 else None

    return list(jobs.values())


# ── seff ──────────────────────────────────────────────────────────────────────

def query_seff(job_id: str) -> dict:
    try:
        result = subprocess.run(
            ["seff", job_id], capture_output=True, text=True, timeout=15
        )
    except FileNotFoundError:
        return {}

    out   = result.stdout
    stats = {}

    m = RE_SEFF_WALL.search(out)
    if m:
        stats["wall_clock"] = m.group(1)

    m = RE_SEFF_CPU_EFF.search(out)
    if m:
        stats["cpu_eff"] = m.group(1)       # e.g. "85.71%"

    m = RE_SEFF_MEM_USE.search(out)
    if m:
        stats["mem_used"] = m.group(1).strip()   # e.g. "1.23 GB"

    m = RE_SEFF_MEM_EFF.search(out)
    if m:
        stats["mem_eff"] = m.group(1)       # e.g. "15.37%"

    m = RE_SEFF_MEM_REQ.search(out)
    if m:
        stats["mem_requested_seff"] = m.group(1).strip()   # e.g. "8.00 GB"

    m = RE_SEFF_CORES.search(out)
    if m:
        stats["alloc_cpus"] = m.group(1)

    return stats


# ── Nextflow config parsing ───────────────────────────────────────────────────

def _extract_block(text: str, marker: str) -> str:
    """
    Return content of the first { } block whose header line starts with marker.
    Uses a line-anchored search to skip occurrences inside comments.
    """
    m = re.search(r"^\s*" + re.escape(marker) + r"\s*\{", text, re.MULTILINE)
    if not m:
        return ""
    brace_start = text.index("{", m.start())
    depth = 0
    for i, c in enumerate(text[brace_start:], brace_start):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start + 1:i]
    return ""


def parse_nf_config(config_path: str) -> dict[str, dict]:
    """
    Parse the longleaf profile's process block from nextflow.config.
    Returns a dict: process_base_name → {cpus, memory, time}.
    """
    text = Path(config_path).read_text()

    longleaf_block = _extract_block(text, "longleaf")
    process_block  = _extract_block(longleaf_block, "process")

    if not process_block:
        return {}

    # Default resource settings — only the lines before any withName block
    default_section = (
        process_block[: process_block.index("withName")]
        if "withName" in process_block else process_block
    )
    defaults = _parse_resource_lines(default_section)

    # withName blocks
    config: dict[str, dict] = {}
    for m in re.finditer(r"withName:\s*['\"]?([^'\"{\n]+)['\"]?\s*\{", process_block):
        names_raw = m.group(1).strip()
        block_start = process_block.find("{", m.start())
        block_content = _extract_block(process_block[block_start - 1:], "")
        settings = _parse_resource_lines(block_content)
        for name in re.split(r"\|", names_raw):
            name = name.strip().strip("'\"")
            config[name] = {**defaults, **settings}

    # Fill in defaults for any process not explicitly listed
    config.setdefault("__default__", defaults)
    return config


def _parse_resource_lines(block: str) -> dict:
    res = {}
    for line in block.splitlines():
        line = line.strip()
        for key in ("cpus", "memory", "time"):
            if re.match(rf"^{key}\s*=", line):
                val = line.split("=", 1)[1].strip().strip("'\"")
                # Normalise Nextflow memory literals: 8.GB → "8 GB"
                val = re.sub(r"(\d+(?:\.\d+)?)\.(GB|MB|KB|TB)", r"\1 \2", val)
                res[key] = val
    return res


def get_config_for(base_name: str, config: dict) -> dict:
    """Return config settings for a process, falling back to __default__."""
    if base_name in config:
        return config[base_name]
    # Try pipe-grouped keys  e.g. "CollectDecodeChunks|CollectCountChunks"
    for key, val in config.items():
        if base_name in key.split("|"):
            return val
    return config.get("__default__", {})


# ── Name normalisation ────────────────────────────────────────────────────────

def base_name(process: str) -> str:
    """'DELI:DecodeChunk (merged)' → 'DecodeChunk'"""
    name = re.sub(r"^[A-Z_]+:", "", process)       # strip subworkflow prefix
    name = re.sub(r"\s*\([^)]*\)$", "", name)      # strip trailing (N)/(merged)
    return name.strip()


# ── Unit helpers ──────────────────────────────────────────────────────────────

def wall_to_min(wall: str) -> float | None:
    """'01:23:45' → 83.75"""
    parts = wall.split(":")
    if len(parts) == 3:
        try:
            h, m, s = parts
            return int(h) * 60 + int(m) + int(s) / 60
        except ValueError:
            pass
    return None


def pct_to_float(pct: str) -> float | None:
    """'85.71%' → 85.71"""
    try:
        return float(pct.rstrip("%"))
    except ValueError:
        return None


def mem_to_gb(mem: str) -> float | None:
    """'1.23 GB' / '512 MB' → float GB"""
    m = re.match(r"([\d.]+)\s*(GB|MB|KB|TB)", mem, re.IGNORECASE)
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2).upper()
    return {"TB": val * 1024, "GB": val, "MB": val / 1024, "KB": val / 1024 / 1024}[unit]


def sem(values: list[float]) -> float | None:
    """Standard error of the mean."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(variance) / math.sqrt(n)


def fmt_float(v: float | None, decimals: int = 2) -> str:
    return f"{v:.{decimals}f}" if v is not None else "-"


# ── Table formatting ──────────────────────────────────────────────────────────

def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    sep = "  "
    header_line = sep.join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(header_line)
    print("-" * len(header_line))
    for row in rows:
        print(sep.join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))


def save_csv(path: str, headers: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)
    print(f"Saved: {path}", file=sys.stderr)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a GitHub-flavoured markdown table."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def fmt_row(cells):
        return "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells)) + " |"

    sep = "| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |"
    lines = [fmt_row(headers), sep] + [fmt_row(r) for r in rows]
    return "\n".join(lines)


def save_report(
    path: str,
    log_path: str,
    start_dt: datetime | None,
    end_dt: datetime | None,
    total_min: float | None,
    total: int,
    completed: int,
    failed: int,
    sum_headers: list[str],
    sum_rows: list[list[str]],
    job_headers: list[str],
    job_rows: list[list[str]],
) -> None:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = []

    lines.append("# DELIVER Pipeline Run Report")
    lines.append("")
    lines.append(f"Generated : {now}")
    lines.append(f"Log       : {log_path}")
    lines.append("")

    # Timing
    lines.append("## Pipeline Timing")
    lines.append("")
    if start_dt and end_dt and total_min is not None:
        h, m = divmod(int(total_min), 60)
        lines.append(f"Start : {start_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        lines.append(f"End   : {end_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        lines.append(f"Total : {h}h {m:02d}m  ({total_min} min)")
    else:
        lines.append("_Timing unavailable — log may be incomplete._")
    lines.append("")

    # Job counts
    lines.append("## Job Counts")
    lines.append("")
    lines.append(f"Total: {total}  |  Completed: {completed}  |  Failed: {failed}  |  Pending/unknown: {total - completed - failed}")
    lines.append("")

    # Summary
    lines.append("## Summary by Process Type")
    lines.append("")
    lines.append(_md_table(sum_headers, sum_rows))
    lines.append("")

    # Per-job detail
    lines.append("## Per-Job Detail")
    lines.append("")
    lines.append(_md_table(job_headers, job_rows))
    lines.append("")

    Path(path).write_text("\n".join(lines))
    print(f"Saved: {path}", file=sys.stderr)


# ── Per-job table ─────────────────────────────────────────────────────────────

def build_job_rows(jobs: list[dict], seff_data: dict) -> tuple[list[str], list[list[str]]]:
    base_headers = ["job_id", "process", "status", "exit", "started", "exited", "dur(min)"]
    seff_headers = ["wall_clock", "cpu_eff", "mem_used", "mem_eff"] if seff_data else []
    headers      = base_headers + seff_headers

    rows = []
    for j in jobs:
        s   = seff_data.get(j["job_id"], {})
        started = j["started"].strftime("%Y-%m-%d %H:%M:%S") if "started" in j else "-"
        exited  = j["exited"].strftime("%Y-%m-%d %H:%M:%S")  if "exited"  in j else "-"
        row = [
            j.get("job_id",    "-"),
            j.get("process",   "-"),
            j.get("status",    "SUBMITTED"),
            j.get("exit_code", "-"),
            started,
            exited,
            str(j["duration_min"]) if j.get("duration_min") is not None else "-",
        ]
        if seff_data:
            row += [
                s.get("wall_clock", "-"),
                s.get("cpu_eff",    "-"),
                s.get("mem_used",   "-"),
                s.get("mem_eff",    "-"),
            ]
        rows.append(row)

    return headers, rows


# ── Summary table ─────────────────────────────────────────────────────────────

def build_summary_rows(
    jobs: list[dict],
    seff_data: dict,
    config: dict,
) -> tuple[list[str], list[list[str]]]:

    # Group jobs by base process name, preserving first-submission order
    groups: dict[str, list] = defaultdict(list)
    proc_order: list[str] = []
    for j in jobs:
        bn = base_name(j["process"])
        if bn not in groups:
            proc_order.append(bn)
        groups[bn].append(j)

    has_seff   = bool(seff_data)
    has_config = bool(config)

    headers = ["process", "n"]
    if has_seff:
        headers += [
            "avg_wall_min", "sem_wall_min",
            "avg_cpu_eff_%", "sem_cpu_eff_%",
            "avg_mem_used_gb", "sem_mem_used_gb",
            "avg_mem_eff_%", "sem_mem_eff_%",
        ]
    else:
        headers += ["avg_dur_min", "sem_dur_min"]
    if has_config:
        headers += ["req_cpus", "req_memory", "req_time"]

    rows = []
    for proc_name in proc_order:
        grp = groups[proc_name]
        row = [proc_name, str(len(grp))]

        def collect(metric, converter):
            return [v for j in grp
                    if j["job_id"] in seff_data
                    for v in [converter(seff_data[j["job_id"]].get(metric, ""))]
                    if v is not None]

        if has_seff:
            walls    = collect("wall_clock", wall_to_min)
            cpu_effs = collect("cpu_eff",    pct_to_float)
            mem_used = collect("mem_used",   mem_to_gb)
            mem_effs = collect("mem_eff",    pct_to_float)

            def avg(vs): return sum(vs) / len(vs) if vs else None

            row += [
                fmt_float(avg(walls)),                fmt_float(sem(walls)),
                fmt_float(avg(cpu_effs)),             fmt_float(sem(cpu_effs)),
                fmt_float(avg(mem_used), 3),          fmt_float(sem(mem_used), 3),
                fmt_float(avg(mem_effs)),             fmt_float(sem(mem_effs)),
            ]
        else:
            durs = [j["duration_min"] for j in grp if j.get("duration_min") is not None]
            def avg(vs): return sum(vs) / len(vs) if vs else None  # noqa: F811
            row += [fmt_float(avg(durs)), fmt_float(sem(durs))]

        if has_config:
            cfg = get_config_for(proc_name, config)
            row += [
                cfg.get("cpus",   "-"),
                cfg.get("memory", "-"),
                cfg.get("time",   "-"),
            ]

        rows.append(row)

    return headers, rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parse Nextflow log and report SLURM job stats."
    )
    parser.add_argument(
        "--log", default=".nextflow.log",
        help="Path to .nextflow.log (default: .nextflow.log)"
    )
    parser.add_argument(
        "--seff", action="store_true",
        help="Query seff per job for wall-clock time, CPU/memory efficiency"
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to nextflow.config to include requested resources in summary"
    )
    parser.add_argument(
        "--output", default=None,
        help="Save per-job table to this CSV file (e.g. jobs.csv)"
    )
    parser.add_argument(
        "--report", default=None,
        help="Save a markdown report to this file (e.g. report.md)"
    )
    args = parser.parse_args()

    jobs = parse_log(args.log)
    if not jobs:
        print("No SLURM jobs found in log.", file=sys.stderr)
        sys.exit(1)

    # Query seff
    seff_data = {}
    if args.seff:
        n = len(jobs)
        for i, j in enumerate(jobs, 1):
            print(
                f"\r[{i}/{n}] {j['job_id']}  {j['process'][:40]}",
                end="", file=sys.stderr, flush=True,
            )
            seff_data[j["job_id"]] = query_seff(j["job_id"])
        print(file=sys.stderr)

    # Parse nextflow config
    nf_config = {}
    if args.config:
        try:
            nf_config = parse_nf_config(args.config)
        except Exception as e:
            print(f"Warning: could not parse config: {e}", file=sys.stderr)

    # Pipeline timing header
    start_dt, end_dt, total_min = parse_pipeline_times(args.log, jobs)
    if start_dt and end_dt and total_min is not None:
        h, m = divmod(int(total_min), 60)
        print(f"Pipeline start : {start_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"Pipeline end   : {end_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"Total wall time: {h}h {m:02d}m  ({total_min} min)")
        print()

    # Per-job table
    job_headers, job_rows = build_job_rows(jobs, seff_data)
    print_table(job_headers, job_rows)

    if args.output:
        save_csv(args.output, job_headers, job_rows)

    # Summary
    total     = len(jobs)
    completed = sum(1 for j in jobs if j.get("status") == "COMPLETED")
    failed    = sum(1 for j in jobs if j.get("status") == "FAILED")
    pending   = total - completed - failed
    print()
    print(f"Total: {total}  Completed: {completed}  Failed: {failed}  Pending/unknown: {pending}")

    # Aggregated summary
    print()
    print("=== Summary by process type ===")
    sum_headers, sum_rows = build_summary_rows(jobs, seff_data, nf_config)
    print_table(sum_headers, sum_rows)

    if args.output:
        stem   = args.output.rsplit(".", 1)[0] if "." in args.output else args.output
        suffix = "." + args.output.rsplit(".", 1)[1] if "." in args.output else ".csv"
        save_csv(stem + "_summary" + suffix, sum_headers, sum_rows)

    if args.report:
        save_report(
            path       = args.report,
            log_path   = args.log,
            start_dt   = start_dt,
            end_dt     = end_dt,
            total_min  = total_min,
            total      = total,
            completed  = completed,
            failed     = failed,
            sum_headers = sum_headers,
            sum_rows    = sum_rows,
            job_headers = job_headers,
            job_rows    = job_rows,
        )


if __name__ == "__main__":
    main()
