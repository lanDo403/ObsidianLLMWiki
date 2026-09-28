"""process_notebook.py — Full pipeline for NotebookLM notebooks.

Fetches curated notes from a NotebookLM notebook and runs them through the
same atomization pipeline used by process.py, so the atomizer sees every
note as a single batch and can build wikilinks across different sources.

Usage:
    # Full pipeline (text notes only):
    python3 scripts/process_notebook.py <notebook_id>

    # Include indexed source fulltext + existing mind map(s):
    python3 scripts/process_notebook.py <notebook_id> --include-sources --include-mindmap

    # Agent-safe write to vault without prompts:
    python3 scripts/process_notebook.py <notebook_id> --non-interactive --on-conflict skip

    # Multi-account setup:
    python3 scripts/process_notebook.py <notebook_id> --profile work

Pipeline:
    fetch_notebook.py -> atomize.py -> generate_notes.py -> vault_writer.py
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from scripts.config import DEFAULT_STAGING_DIR, load_config
except ModuleNotFoundError:
    from config import DEFAULT_STAGING_DIR, load_config

SCRIPTS_DIR = Path(__file__).parent


def run(cmd: list[str], desc: str) -> str:
    """Run a subprocess, pass stderr through, and return stripped stdout."""
    print(f">> {desc}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        print(
            f"ERROR: Step '{desc}' failed with exit code {result.returncode}.",
            file=sys.stderr,
        )
        if result.stdout:
            print(f"stdout: {result.stdout}", file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout.strip()


def create_run_staging_dir(base_dir: str, name_hint: str) -> str:
    path = Path(base_dir)
    path.mkdir(parents=True, exist_ok=True)
    prefix = name_hint[:40].strip() or "nblm"
    return tempfile.mkdtemp(prefix=f"{prefix}-", dir=path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Full ObsidianLLMWIKI pipeline for NotebookLM: "
            "fetch_notebook.py -> atomize.py -> generate_notes.py -> vault_writer.py"
        )
    )
    parser.add_argument("notebook_id", help="NotebookLM notebook ID")
    parser.add_argument(
        "--include-sources",
        action="store_true",
        help="Include indexed source fulltext as extra sections",
    )
    parser.add_argument(
        "--include-mindmap",
        action="store_true",
        help="Include existing mind map(s) as an extra section",
    )
    parser.add_argument(
        "--profile",
        help="NotebookLM profile name (for multi-account setups)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable prompts during vault writes and use --on-conflict policy",
    )
    parser.add_argument(
        "--on-conflict",
        choices=("skip", "overwrite"),
        default="skip",
        help="Duplicate note policy for vault writes in non-interactive mode (default: skip)",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "claude", "codex"),
        default="auto",
        help="Rewrite backend for atomization (default: auto-detect)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="Timeout for each rewrite backend call (default: 300)",
    )
    args = parser.parse_args()

    fetch_notebook_py = str(SCRIPTS_DIR / "fetch_notebook.py")
    atomize_py = str(SCRIPTS_DIR / "atomize.py")
    generate_notes_py = str(SCRIPTS_DIR / "generate_notes.py")
    vault_writer_py = str(SCRIPTS_DIR / "vault_writer.py")

    # ── Step 1: fetch_notebook.py ─────────────────────────────────────────────
    fetch_cmd = [sys.executable, fetch_notebook_py, args.notebook_id]
    if args.include_sources:
        fetch_cmd.append("--include-sources")
    if args.include_mindmap:
        fetch_cmd.append("--include-mindmap")
    if args.profile:
        fetch_cmd.extend(["--profile", args.profile])

    parsed_json_path = run(
        fetch_cmd,
        desc=f"fetch_notebook: pull notes from '{args.notebook_id}'",
    )

    # ── Step 2: atomize.py ────────────────────────────────────────────────────
    atom_plan_path = run(
        [
            sys.executable, atomize_py, parsed_json_path,
            "--backend", args.backend,
            "--timeout-seconds", str(args.timeout_seconds),
        ],
        desc="atomize: notebook JSON -> atom plan (cross-linked)",
    )

    # ── Step 3: generate_notes.py ─────────────────────────────────────────────
    cfg = load_config()
    staging_root = cfg.get("rclone", {}).get("staging_dir", DEFAULT_STAGING_DIR)
    run_staging_dir = create_run_staging_dir(staging_root, Path(atom_plan_path).stem)
    staging_dir = run(
        [sys.executable, generate_notes_py, atom_plan_path, "--staging-dir", run_staging_dir],
        desc="generate_notes: atom plan -> staging .md files",
    )

    # ── Step 4: vault_writer.py ───────────────────────────────────────────────
    vw_cmd = [
        sys.executable, vault_writer_py,
        "--staging", staging_dir,
        "--atom-plan", atom_plan_path,
    ]
    if args.non_interactive:
        vw_cmd.extend(["--non-interactive", "--on-conflict", args.on_conflict])
    summary = run(vw_cmd, desc="vault_writer: staging -> vault")
    print(summary)


if __name__ == "__main__":
    main()
