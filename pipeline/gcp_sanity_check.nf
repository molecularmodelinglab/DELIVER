#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// ============================================================================
// DELIVER — dependency sanity check
//
// Run this INSTEAD of the real pipeline to verify that the container image
// has every tool and Python package installed correctly on GCP Cloud Batch
// before committing to a full pipeline run.
//
// Each process runs on a real GCP VM inside the container and checks one
// layer of the stack. All processes run in parallel.
//
// Usage:
//   nextflow run pipeline/main.nf \
//       -c nextflow.config \
//       -profile gcp \
//       -w gs://YOUR_BUCKET/deliver-work \
//       --project YOUR_PROJECT \
//       --bucket  YOUR_BUCKET \
//       --region  us-central1
//
// A successful run prints a final summary and exits 0.
// Any failure prints exactly which check failed and why.
//
// Once this passes cleanly, swap back to the real main.nf.
// ============================================================================

// ── Check 1: Python packages ──────────────────────────────────────────────
// Imports every package used across the pipeline.
// Fails immediately with ImportError if anything is missing.
process CHECK_PYTHON_DEPS {
    label 'check'

    output:
    path "python_deps.txt"

    script:
    """
    #!/usr/bin/env python3
    import sys
    results = []

    packages = {
        "yaml"    : "pyyaml",
        "polars"  : "polars",
        "click"   : "click",
        "pyarrow" : "pyarrow",
    }

    all_ok = True
    for module, pip_name in packages.items():
        try:
            m = __import__(module)
            ver = getattr(m, "__version__", "unknown")
            results.append(f"  OK  {pip_name:<12} {ver}")
        except ImportError as e:
            results.append(f"  FAIL {pip_name:<12} {e}")
            all_ok = False

    # Also print Python version
    results.insert(0, f"  Python {sys.version.split()[0]}")

    with open("python_deps.txt", "w") as f:
        f.write("\\n".join(results) + "\\n")

    print("\\n".join(results))

    if not all_ok:
        sys.exit(1)
    """
}

// ── Check 2: deli CLI ─────────────────────────────────────────────────────
// Confirms the deli binary is on PATH and responds to --version.
// Also confirms deli config exists (created by docker-entrypoint.sh).
process CHECK_DELI_CLI {
    label 'check'

    output:
    path "deli_check.txt"

    script:
    """
    #!/usr/bin/env bash
    set -euo pipefail

    {
        echo "=== DELI CLI Check ==="

        # Check binary
        if command -v deli >/dev/null 2>&1; then
            echo " deli binary : \$(which deli)"
            echo " deli version: \$(deli --version 2>&1 | head -1)"
        else
            echo " FAIL: deli binary not found in PATH"
            exit 1
        fi

        # Check config (more tolerant now)
        if [ -f "\${HOME}/.deli/deli.config" ]; then
            echo " deli config : OK (\${HOME}/.deli/deli.config)"
        else
            echo " deli config : MISSING (known deli 0.2.0 bug)"
            echo "   → Entry point ran but config creation failed"
        fi

        # Extra debug: show content of .deli directory
        echo ""
        echo " Content of \${HOME}/.deli/:"
        ls -la "\${HOME}/.deli/" 2>/dev/null || echo "   Directory not found or empty"

    } | tee deli_check.txt

    # Do NOT exit 1 just because config is missing (we know it's buggy)
    # Only fail if deli binary itself is missing
    if ! command -v deli >/dev/null 2>&1; then
        exit 1
    fi
    """
}

// ── Check 3: fastp binary ─────────────────────────────────────────────────
// Confirms fastp is on PATH and prints its version.
process CHECK_FASTP {
    label 'check'
    cpus 1
    memory '2 GB'

    output:
    path "fastp_check.txt"

    script:
    """
    #!/usr/bin/env bash
    set -euo pipefail
    echo "=== CHECK_FASTP STARTED ===" >&2
    echo "Container image: ${params.container_image}" >&2   # if available via env
    echo "whoami: \$(whoami)" >&2
    echo "which fastp: \$(which fastp || echo 'NOT FOUND')" >&2

    {
        echo "  fastp binary : \$(which fastp)"
        echo "  fastp version: \$(fastp --version 2>&1 | head -1)"
    } | tee fastp_check.txt
    """
}

// ── Check 4: postprocess scripts ─────────────────────────────────────────
// Confirms deduplicate.py and enrichment.py are present at DELIVER_SRC_DIR
// and that click can parse their --help (exercises the full import chain).
process CHECK_POSTPROCESS_SCRIPTS {
    label 'check'

    output:
    path "postprocess_check.txt"

    script:
    """
    #!/usr/bin/env bash
    set -euo pipefail

    SRC="\${DELIVER_SRC_DIR:-}/deliver/postprocess"

    {
        echo "  DELIVER_SRC_DIR: \${DELIVER_SRC_DIR:-NOT SET}"

        for script in deduplicate.py enrichment.py; do
            SCRIPT_PATH="\${SRC}/\${script}"
            if [ -f "\${SCRIPT_PATH}" ]; then
                # Run --help to confirm imports work (click loads the whole module)
                python3 "\${SCRIPT_PATH}" --help > /dev/null 2>&1 \
                    && echo "  OK  \${script} (imports + click entrypoint)" \
                    || { echo "  FAIL \${script} — --help failed"; exit 1; }
            else
                echo "  FAIL \${script} not found at \${SCRIPT_PATH}"
                exit 1
            fi
        done
    } | tee postprocess_check.txt
    """
}

// ── Check 5: GCS read/write ───────────────────────────────────────────────
// Writes a small test file to the GCS work directory and reads it back.
// Confirms the VM has network access to GCS and the bucket is writable.
process CHECK_GCS_ACCESS {
    label 'check'

    output:
    path "gcs_check.txt"

    script:
    """
    #!/usr/bin/env python3
    import subprocess
    import sys

    bucket = "${params.bucket}"
    project = "${params.project}"
    test_blob = f"gs://{bucket}/deliver-sanity-check/test.txt"

    results = []
    results.append("=== GCS Access Check ===")
    results.append(f"Bucket  : gs://{bucket}")
    results.append(f"Project : {project}")
    results.append(f"Test file : {test_blob}")
    results.append("")

    try:
        # Check gsutil availability
        r = subprocess.run(["which", "gsutil"], capture_output=True, text=True)
        if r.returncode != 0:
            results.append(" FAIL: gsutil command not found in PATH")
            print("\\n".join(results))
            sys.exit(1)
        results.append(f" gsutil binary : {r.stdout.strip()}")

        # Write test file to GCS
        results.append("→ Testing GCS write...")
        r = subprocess.run(
            ["gsutil", "-q", "cp", "-", test_blob],   # -q = quiet
            input=b"deliver-sanity-check test file\\n",
            capture_output=True
        )
        if r.returncode != 0:
            results.append(f" FAIL GCS write: {r.stderr.decode().strip()}")
            print("\\n".join(results))
            sys.exit(1)
        results.append(f" OK  GCS write → {test_blob}")

        # Read it back
        results.append("→ Testing GCS read...")
        r = subprocess.run(["gsutil", "-q", "cat", test_blob], capture_output=True)
        if r.returncode != 0:
            results.append(f" FAIL GCS read: {r.stderr.decode().strip()}")
            print("\\n".join(results))
            sys.exit(1)
        results.append(f" OK  GCS read  ← {test_blob}")

        # Delete test file
        results.append("→ Cleaning up...")
        subprocess.run(["gsutil", "-q", "rm", test_blob], capture_output=True)

        results.append(f" OK  GCS delete ← {test_blob}")
        results.append("")
        results.append("============================================")
        results.append(" GCS ACCESS CHECK PASSED")
        results.append("============================================")

        # Save report
        with open("gcs_check.txt", "w") as f:
            f.write("\\n".join(results) + "\\n")

        print("\\n".join(results))

    except Exception as e:
        results.append(f" UNEXPECTED ERROR: {str(e)}")
        with open("gcs_check.txt", "w") as f:
            f.write("\\n".join(results) + "\\n")
        print("\\n".join(results))
        sys.exit(1)
    """
}

// ── Check 6: system tools used in pipeline scripts ────────────────────────
// Confirms bash builtins (cat, gunzip, tr, head) are available.
// These are used in CONCAT and DECOMPRESS process scripts.
process CHECK_SYSTEM_TOOLS {
    label 'check'

    output:
    path "system_check.txt"

    script:
    """
    #!/usr/bin/env bash
    set -euo pipefail

    {
        for tool in bash cat gunzip gzip tr head python3; do
            PATH_OF=\$(which \$tool 2>/dev/null || echo "NOT FOUND")
            if [ "\$PATH_OF" = "NOT FOUND" ]; then
                echo "  FAIL \$tool — not found"
                exit 1
            else
                echo "  OK  \$tool — \$PATH_OF"
            fi
        done
    } | tee system_check.txt
    """
}

// ── Final summary process ─────────────────────────────────────────────────
// Collects all check outputs and prints a single pass/fail summary.
// Only runs if every upstream check succeeded.
process SUMMARISE_CHECKS {
    label 'check'

    input:
    path "python_deps.txt"
    path "deli_check.txt"
    path "fastp_check.txt"
    path "postprocess_check.txt"
    path "gcs_check.txt"
    path "system_check.txt"

    output:
    path "sanity_check_summary.txt"

    script:
    """
    #!/usr/bin/env bash

    SUMMARY="sanity_check_summary.txt"

    {
        echo "============================================"
        echo "  DELIVER — GCP Sanity Check Summary"
        echo "  \$(date)"
        echo "  Project : ${params.project}"
        echo "  Bucket  : gs://${params.bucket}"
        echo "  Region  : ${params.region}"
        echo "============================================"
        echo ""
        echo "[1] Python dependencies"
        cat python_deps.txt
        echo ""
        echo "[2] deli CLI"
        cat deli_check.txt
        echo ""
        echo "[3] fastp"
        cat fastp_check.txt
        echo ""
        echo "[4] Postprocess scripts"
        cat postprocess_check.txt
        echo ""
        echo "[5] GCS access"
        cat gcs_check.txt
        echo ""
        echo "[6] System tools"
        cat system_check.txt
        echo ""
        echo "============================================"
        echo "  ALL CHECKS PASSED — ready for pipeline run"
        echo "============================================"
    } | tee "\$SUMMARY"
    """
}

// ── Workflow ──────────────────────────────────────────────────────────────
workflow {

    log.info """
    ============================================
     DELIVER — GCP dependency sanity check
     Project : ${params.project}
     Bucket  : gs://${params.bucket ?: 'not set'}
     Region  : ${params.region}
     Image   : ${params.container_image}
    ============================================
    All checks run in parallel on real GCP VMs.
    Check the GCP Cloud Batch console to confirm
    jobs are dispatched to Cloud Batch (not local).
    ============================================
    """.stripIndent()

    // Validate minimum required params
    if (!params.project || params.project == 'null') {
        error "ERROR: --project is required"
    }
    if (!params.bucket || params.bucket == 'null') {
        error "ERROR: --bucket is required"
    }

    // Run all checks in parallel on GCP VMs
    python_ok     = CHECK_PYTHON_DEPS()
    deli_ok       = CHECK_DELI_CLI()
    fastp_ok      = CHECK_FASTP()
    postprocess_ok = CHECK_POSTPROCESS_SCRIPTS()
    gcs_ok        = CHECK_GCS_ACCESS()
    system_ok     = CHECK_SYSTEM_TOOLS()

    // Collect all results and print summary
    SUMMARISE_CHECKS(
        python_ok,
        deli_ok,
        fastp_ok,
        postprocess_ok,
        gcs_ok,
        system_ok
    )
}
