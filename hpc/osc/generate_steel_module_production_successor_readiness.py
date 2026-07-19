#!/usr/bin/env python3
"""Generate the inert R4 lock candidate from a sealed formal R3 bundle.

The output is not authority by itself.  It becomes usable only after human
review and a clean Git commit whose sole delta from the frozen R2 control
commit is the tracked recovery-readiness lock.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from steel_module_production_phase2b_lib import (
    load_phase2a_lock,
    write_exclusive_json,
)
from steel_module_production_successor_lib import (
    FORMAL_SUCCESSOR_EXECUTION_NAME,
    RECOVERY_READINESS_LOCK_RELATIVE,
    build_successor_recovery_readiness_candidate,
    load_successor_execution,
    validate_successor_r2_boundary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--preflight-evidence-dir", required=True, type=Path)
    parser.add_argument("--write-proposal", required=True, type=Path)
    return parser.parse_args()


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def collect(
    *, repo_root: Path, preflight_evidence_dir: Path
) -> dict[str, object]:
    root = repo_root.resolve()
    readiness = root / RECOVERY_READINESS_LOCK_RELATIVE
    if readiness.exists() or readiness.is_symlink():
        raise ValueError("R4 recovery-readiness lock already exists")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("R4 proposal requires a clean R2 checkout")
    _, phase2a = load_phase2a_lock(root)
    execution_dir = (
        Path(phase2a["canonical_directory"]).parent
        / FORMAL_SUCCESSOR_EXECUTION_NAME
    )
    execution = load_successor_execution(
        execution_dir,
        repo_root=root,
        require_readiness=False,
        verify_live_predecessor=True,
    )
    validate_successor_r2_boundary(execution, repo_root=root)
    control = execution.manifest["sources"]["control_plane"]
    if (
        _git(root, "rev-parse", "HEAD") != control["git_commit"]
        or _git(root, "rev-parse", "HEAD^{tree}") != control["git_tree"]
    ):
        raise ValueError("live checkout is not the successor's frozen R2 source")
    return build_successor_recovery_readiness_candidate(
        execution, preflight_evidence_dir=preflight_evidence_dir
    )


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        proposal = collect(
            repo_root=root,
            preflight_evidence_dir=args.preflight_evidence_dir,
        )
        target = args.write_proposal.expanduser()
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError("R4 proposal path must not be a symlink")
        target = target.parent.resolve() / target.name
        if target == root / RECOVERY_READINESS_LOCK_RELATIVE or root in target.parents:
            raise ValueError("R4 proposal must remain outside the repository")
        execution_root = Path(
            proposal["successor_execution"]["directory"]  # type: ignore[index]
        ).resolve()
        if target == execution_root or execution_root in target.parents:
            raise ValueError(
                "R4 proposal must remain outside the managed execution companion"
            )
        if not target.parent.is_dir():
            raise ValueError("R4 proposal parent directory is missing")
        write_exclusive_json(target, proposal)
        print(f"Phase-2B recovery readiness candidate: {target}")
        print(f"readiness_hash: {proposal['readiness_hash']}")
        print(
            "Candidate is non-authoritative until reviewed and committed as the "
            "sole R4 lock-only delta."
        )
        print("No scheduler command was invoked.")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"Cannot generate steel-module recovery readiness candidate: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
