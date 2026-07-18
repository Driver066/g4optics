#!/usr/bin/env python3
"""Immutable BC-only production-checkpoint helpers.

The checkpoint is deliberately a small, content-addressed index.  It never
copies ROOT files: every referenced event file remains in the checksum-valid
managed-child finalization that produced it.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from steel_module_campaign_lib import (
    canonical_json,
    load_json,
    require_dict,
    require_sha256,
    require_string,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_phase2b_lib import (
    FORMAL_EXECUTION_NAME,
    READINESS_LOCK_RELATIVE,
    READINESS_LOCK_SCHEMA_VERSION,
    load_execution_companion,
    load_phase2a_lock,
)


CHECKPOINT_SCHEMA_VERSION = "steel-module-production-checkpoint-v1"
MANAGED_FINALIZATION_SCHEMA_VERSION = "steel-module-managed-finalization-v1"
FORMAL_CHECKPOINT_STATE = "BC-ONLY-S1"
FORMAL_EVIDENCE_MODE = "back-center-only"
CHECKPOINT_REQUIRED_FILES = {
    "checkpoint.json",
    "configuration_summary.csv",
    "event_audit.json",
    "seed_audit.json",
    "source_children.json",
    "source_finalization.json",
    "task_index.tsv",
}


@dataclass(frozen=True)
class ProductionCheckpoint:
    directory: Path
    manifest: dict[str, Any]
    task_rows: tuple[dict[str, str], ...]
    event_audit: dict[str, Any]
    seed_audit: dict[str, Any]

    @property
    def checkpoint_hash(self) -> str:
        return require_sha256(self.manifest, "checkpoint_hash")


def _safe_relative_name(raw: str) -> Path:
    relative = Path(raw)
    if (
        not raw
        or relative.is_absolute()
        or ".." in relative.parts
        or str(relative) != raw
    ):
        raise ValueError(f"unsafe checksum-manifest path: {raw!r}")
    return relative


def recursive_file_records(
    directory: Path, *, exclude: Iterable[str] = ("SHA256SUMS",)
) -> dict[str, str]:
    """Return the exact recursive regular-file set, rejecting all symlinks."""

    root = directory.resolve()
    excluded = set(exclude)
    records: dict[str, str] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                raise ValueError(f"symlink is forbidden in evidence tree: {path}")
        for name in files:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative in excluded:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"non-regular evidence file: {path}")
            records[relative] = sha256_file(path)
    return dict(sorted(records.items()))


def write_recursive_checksums(directory: Path) -> None:
    records = recursive_file_records(directory)
    text = "".join(f"{digest}  {name}\n" for name, digest in records.items())
    path = directory / "SHA256SUMS"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o444)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def publish_directory_no_replace(temporary: Path, target: Path) -> None:
    """Publish a completed sibling tree without an internal writer race.

    The exclusive sibling lock makes concurrent Phase-2B writers fail closed.
    A stale lock is intentionally not guessed away after a process crash.
    """

    if temporary.parent != target.parent:
        raise ValueError("atomic publication requires sibling directories")
    lock = target.parent / f".{target.name}.publish.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"publication is already active or quarantined: {lock}") from exc
    try:
        if target.exists() or target.is_symlink():
            raise ValueError(f"refusing to overwrite evidence directory: {target}")
        os.rename(temporary, target)
        parent_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        lock.rmdir()


def verify_recursive_checksums(
    directory: Path, *, required: Iterable[str] = ()
) -> dict[str, str]:
    """Verify both digest values and the complete recursive file set."""

    root = directory.expanduser()
    if root.is_symlink():
        raise ValueError(f"evidence directory must not be a symlink: {root}")
    root = root.resolve()
    manifest_path = root / "SHA256SUMS"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError(f"missing regular checksum manifest: {manifest_path}")
    recorded: dict[str, str] = {}
    for line_number, raw in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        pieces = raw.split("  ", 1)
        if len(pieces) != 2:
            raise ValueError(f"invalid SHA256SUMS row {line_number}: {raw!r}")
        digest, name = pieces
        relative = _safe_relative_name(name)
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or name in recorded
            or name == "SHA256SUMS"
        ):
            raise ValueError(f"invalid SHA256SUMS identity: {raw!r}")
        path = root / relative
        if path.is_symlink() or not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"checksum mismatch: {name}")
        recorded[name] = digest
    actual = recursive_file_records(root)
    if actual != recorded:
        missing = sorted(set(recorded) - set(actual))
        unexpected = sorted(set(actual) - set(recorded))
        raise ValueError(
            "recursive checksum file set mismatch: "
            f"missing={missing}, unexpected={unexpected}"
        )
    absent = sorted(set(required) - set(recorded))
    if absent:
        raise ValueError(f"checksum manifest lacks required files: {absent}")
    return recorded


def read_tsv(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = tuple(reader)
    if not reader.fieldnames:
        raise ValueError(f"TSV has no header: {path}")
    return rows


def _require_int(row: dict[str, str], key: str) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"task index has invalid {key}: {row.get(key)!r}") from exc


def validate_bc_only_s1_rows(
    rows: Iterable[dict[str, str]], *, verify_root_files: bool
) -> tuple[dict[str, str], ...]:
    """Enforce the exact formal BC-S1 prefix and all referenced ROOT hashes."""

    task_rows = tuple(rows)
    if len(task_rows) != 32:
        raise ValueError(f"BC-ONLY-S1 requires exactly 32 tasks, got {len(task_rows)}")
    logical_ids = [row.get("logical_task_id", "") for row in task_rows]
    if any(not value for value in logical_ids) or len(set(logical_ids)) != 32:
        raise ValueError("checkpoint logical task IDs must be non-empty and unique")
    if [ _require_int(row, "task_index") for row in task_rows ] != list(range(1, 33)):
        raise ValueError("BC-ONLY-S1 task indexes must be contiguous 1..32")

    seen_config_blocks: set[tuple[int, int]] = set()
    production_seeds: list[int] = []
    for row in task_rows:
        thickness = _require_int(row, "tile_thickness_mm")
        block = _require_int(row, "seed_block")
        if (
            row.get("stage") != "production"
            or row.get("sipm_layout") != "back-center"
            or _require_int(row, "absorber_transverse_mm") != 500
            or _require_int(row, "x_mm") != 0
            or _require_int(row, "y_mm") != 0
            or _require_int(row, "events") != 250
            or thickness not in (4, 24)
            or block not in range(16)
        ):
            raise ValueError(
                f"task is outside exact BC-ONLY-S1 shape: {row.get('logical_task_id')}"
            )
        key = (thickness, block)
        if key in seen_config_blocks:
            raise ValueError(f"duplicate thickness/block in checkpoint: {key}")
        seen_config_blocks.add(key)
        production_seeds.extend(
            (_require_int(row, "seed1"), _require_int(row, "seed2"))
        )
        for hash_key in (
            "configuration_hash",
            "task_result_sha256",
            "run_config_sha256",
            "root_sha256",
            "summary_sha256",
        ):
            digest = row.get(hash_key, "")
            if len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise ValueError(f"invalid {hash_key} for {row.get('logical_task_id')}")
        root_path = Path(row.get("root", ""))
        if not root_path.is_absolute():
            raise ValueError("checkpoint ROOT references must be absolute")
        if verify_root_files:
            if root_path.is_symlink() or not root_path.is_file():
                raise ValueError(f"missing regular ROOT evidence: {root_path}")
            if sha256_file(root_path) != row["root_sha256"]:
                raise ValueError(f"ROOT checksum mismatch: {root_path}")

    expected = {(thickness, block) for thickness in (4, 24) for block in range(16)}
    if seen_config_blocks != expected:
        raise ValueError("BC-ONLY-S1 contains a block gap or unexpected block")
    if len(production_seeds) != 64 or len(set(production_seeds)) != 64:
        raise ValueError("BC-ONLY-S1 must contain exactly 64 unique production seeds")
    return task_rows


def _semantic_hash(record: dict[str, Any], field: str) -> str:
    expected = require_sha256(record, field)
    unhashed = dict(record)
    unhashed.pop(field)
    if sha256_bytes(canonical_json(unhashed)) != expected:
        raise ValueError(f"{field} semantic hash mismatch")
    return expected


def load_managed_finalization(
    finalized_dir: Path, *, allow_test_mode: bool = False, verify_root_files: bool = True
) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
    directory = finalized_dir.expanduser()
    if directory.is_symlink():
        raise ValueError("managed finalization directory must not be a symlink")
    directory = directory.resolve()
    required = {
        "task_index.tsv",
        "configuration_summary.csv",
        "event_audit.json",
        "seed_audit.json",
        "selection_record.json",
        "validation_report.json",
    }
    verify_recursive_checksums(directory, required=required)
    validation = load_json(directory / "validation_report.json")
    if validation.get("schema_version") != MANAGED_FINALIZATION_SCHEMA_VERSION:
        raise ValueError("unsupported managed finalization schema")
    if validation.get("valid") is not True:
        raise ValueError("managed finalization is not valid")
    test_mode = validation.get("test_mode") is True
    accepted = validation.get("accepted_statistical_evidence") is True
    if accepted == test_mode:
        raise ValueError("managed finalization acceptance/test-mode flags disagree")
    if test_mode and not allow_test_mode:
        raise ValueError("formal checkpoint loader refuses test-mode finalization")
    if (
        validation.get("child_id") != "BC-S1"
        or validation.get("task_count") != 32
        or validation.get("event_count") != 8_000
        or validation.get("event_audit_integrated") is not True
        or validation.get("seed_audit_integrated") is not True
    ):
        raise ValueError("managed finalization is not the complete BC-S1 child")
    rows = validate_bc_only_s1_rows(
        read_tsv(directory / "task_index.tsv"),
        verify_root_files=verify_root_files,
    )
    return validation, rows


def load_production_checkpoint(
    checkpoint_dir: Path,
    *,
    repo_root: Path | None = None,
    allow_test_mode: bool = False,
    require_readiness: bool = True,
    verify_root_files: bool = True,
) -> ProductionCheckpoint:
    """Load a formal or explicit test checkpoint.

    Formal evidence revalidates the tracked readiness lock directly.  The
    copied lock digest is provenance, not a substitute for tracked authority.
    """
    directory = checkpoint_dir.expanduser()
    if directory.is_symlink():
        raise ValueError("production checkpoint directory must not be a symlink")
    directory = directory.resolve()
    checksums = verify_recursive_checksums(
        directory, required=CHECKPOINT_REQUIRED_FILES
    )
    manifest = load_json(directory / "checkpoint.json")
    if manifest.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("unsupported production checkpoint schema")
    checkpoint_hash = _semantic_hash(manifest, "checkpoint_hash")
    if directory.name != f"bc-only-s1-{checkpoint_hash[:12]}":
        raise ValueError("checkpoint directory name does not match checkpoint hash")
    if (
        manifest.get("state_id") != FORMAL_CHECKPOINT_STATE
        or manifest.get("evidence_mode") != FORMAL_EVIDENCE_MODE
        or manifest.get("child_ids") != ["BC-S1"]
        or manifest.get("task_count") != 32
        or manifest.get("event_count") != 8_000
        or manifest.get("root_tree") != "scan"
    ):
        raise ValueError("checkpoint is not exact BC-ONLY-S1 evidence")
    test_mode = manifest.get("test_mode") is True
    accepted = manifest.get("accepted_statistical_evidence") is True
    if accepted == test_mode:
        raise ValueError("checkpoint acceptance/test-mode flags disagree")
    if test_mode and not allow_test_mode:
        raise ValueError("formal loader refuses a test-mode checkpoint")
    readiness = require_dict(manifest, "readiness")
    if require_readiness and (
        readiness.get("required") is not True
        or not isinstance(readiness.get("lock_sha256"), str)
        or len(readiness["lock_sha256"]) != 64
    ):
        raise ValueError("checkpoint lacks the required Phase-2B readiness identity")
    if require_readiness and not test_mode:
        if repo_root is None:
            raise ValueError("formal checkpoint validation requires repo_root")
        readiness_path = repo_root.expanduser().resolve() / READINESS_LOCK_RELATIVE
        if readiness_path.is_symlink() or not readiness_path.is_file():
            raise ValueError("formal checkpoint readiness lock is missing or unsafe")
        readiness_lock = load_json(readiness_path)
        if readiness_lock.get("schema_version") != READINESS_LOCK_SCHEMA_VERSION:
            raise ValueError("unsupported Phase-2B readiness-lock schema")
        if (
            readiness_lock.get("status") != "accepted-formal-phase-2b-ready"
            or readiness_lock.get("managed_submission_ready") is not True
            or readiness_lock.get("automatic_submission") is not False
            or readiness_lock.get("real_slurm_submission_performed") is not False
            or readiness_lock.get("formal_intent_count") != 0
            or readiness_lock.get("slurm_job_ids") != []
        ):
            raise ValueError(
                "tracked Phase-2B readiness lock is not an accepted zero-submit lock"
            )
        if sha256_file(readiness_path) != readiness.get("lock_sha256"):
            raise ValueError("checkpoint readiness-lock digest is stale")
        execution = require_dict(manifest, "execution")
        locked_execution = require_dict(readiness_lock, "execution_companion")
        for key in ("execution_id", "execution_hash"):
            if locked_execution.get(key) != execution.get(key):
                raise ValueError(f"checkpoint readiness execution {key} mismatch")
        _, phase2a = load_phase2a_lock(repo_root)
        canonical_child = Path(require_string(phase2a, "canonical_directory")).resolve()
        canonical_execution = canonical_child.parent / FORMAL_EXECUTION_NAME
        canonical_root = canonical_child.parent / "steel-module-production-checkpoints"
        if directory.parent != canonical_root:
            raise ValueError(f"formal checkpoint path must be under {canonical_root}")
        if execution.get("directory") != str(canonical_execution):
            raise ValueError("checkpoint execution directory is not canonical")
        loaded_execution = load_execution_companion(
            canonical_execution,
            repo_root=repo_root,
            allow_test_mode=False,
            require_readiness=True,
            verify_runtime=True,
        )
        for key, actual in (
            ("execution_id", loaded_execution.execution_id),
            ("execution_hash", loaded_execution.execution_hash),
        ):
            if execution.get(key) != actual:
                raise ValueError(f"checkpoint managed execution {key} mismatch")
        managed_child = require_dict(loaded_execution.manifest, "managed_child")
        for checkpoint_key, child_key in (
            ("task_set_hash", "task_set_hash"),
            ("task_seed_mapping_hash", "task_seed_mapping_hash"),
            ("production_seed_set_hash", "seed_set_hash"),
        ):
            if require_sha256(manifest, checkpoint_key) != require_sha256(
                managed_child, child_key
            ):
                raise ValueError(f"checkpoint {checkpoint_key} mismatch")
    rows = validate_bc_only_s1_rows(
        read_tsv(directory / "task_index.tsv"), verify_root_files=verify_root_files
    )
    if sha256_file(directory / "task_index.tsv") != manifest.get(
        "task_index_sha256"
    ):
        raise ValueError("checkpoint task-index identity mismatch")
    event_audit = load_json(directory / "event_audit.json")
    seed_audit = load_json(directory / "seed_audit.json")
    if event_audit.get("valid") is not True or event_audit.get("task_count") != 32:
        raise ValueError("checkpoint event audit is invalid")
    if (
        seed_audit.get("valid") is not True
        or seed_audit.get("production_seed_count") != 64
        or seed_audit.get("sealed_pilot_seed_count") != 240
        or seed_audit.get("overlap_count") != 0
    ):
        raise ValueError("checkpoint seed audit is invalid")
    source = load_json(directory / "source_finalization.json")
    if source != require_dict(manifest, "source_finalization"):
        raise ValueError("checkpoint source-finalization record mismatch")
    if require_readiness and not test_mode:
        finalized = canonical_execution / "finalized"
        if source.get("directory") != str(finalized):
            raise ValueError("checkpoint source finalization is not canonical")
        validation, source_rows = load_managed_finalization(
            finalized,
            allow_test_mode=False,
            verify_root_files=verify_root_files,
        )
        if source_rows != rows:
            raise ValueError("checkpoint rows differ from canonical source finalization")
        if (
            source.get("checksum_manifest_sha256")
            != sha256_file(finalized / "SHA256SUMS")
            or source.get("finalization_hash") != validation.get("finalization_hash")
        ):
            raise ValueError("checkpoint source-finalization identity changed")
    source_children = load_json(directory / "source_children.json")
    if source_children != require_dict(manifest, "source_children"):
        raise ValueError("checkpoint source-children record mismatch")
    for name, digest in (
        ("event_audit.json", manifest.get("event_audit_sha256")),
        ("seed_audit.json", manifest.get("seed_audit_sha256")),
        ("source_children.json", manifest.get("source_children_sha256")),
        ("source_finalization.json", manifest.get("source_finalization_sha256")),
    ):
        if checksums.get(name) != digest:
            raise ValueError(f"checkpoint embedded artifact identity mismatch: {name}")
    return ProductionCheckpoint(directory, manifest, rows, event_audit, seed_audit)


def write_checkpoint_atomic(
    output_root: Path,
    *,
    manifest_payload: dict[str, Any],
    task_index_text: str,
    configuration_summary_text: str,
    event_audit: dict[str, Any],
    seed_audit: dict[str, Any],
    source_children: dict[str, Any],
    source_finalization: dict[str, Any],
) -> Path:
    """Write one non-overwritable content-addressed checkpoint directory."""

    requested_root = output_root.expanduser()
    if requested_root.is_symlink():
        raise ValueError("checkpoint output root must not be a symlink")
    root = requested_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    payload = dict(manifest_payload)
    if "checkpoint_hash" in payload:
        raise ValueError("checkpoint payload must not pre-populate checkpoint_hash")
    payload["checkpoint_hash"] = sha256_bytes(canonical_json(payload))
    target = root / f"bc-only-s1-{payload['checkpoint_hash'][:12]}"
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite checkpoint: {target}")
    temp = Path(tempfile.mkdtemp(prefix=".bc-only-s1.tmp-", dir=root))
    try:
        (temp / "task_index.tsv").write_text(task_index_text, encoding="utf-8")
        (temp / "configuration_summary.csv").write_text(
            configuration_summary_text, encoding="utf-8"
        )
        for name, value in (
            ("event_audit.json", event_audit),
            ("seed_audit.json", seed_audit),
            ("source_children.json", source_children),
            ("source_finalization.json", source_finalization),
            ("checkpoint.json", payload),
        ):
            (temp / name).write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        write_recursive_checksums(temp)
        publish_directory_no_replace(temp, target)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return target


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "FORMAL_CHECKPOINT_STATE",
    "MANAGED_FINALIZATION_SCHEMA_VERSION",
    "ProductionCheckpoint",
    "load_managed_finalization",
    "load_production_checkpoint",
    "publish_directory_no_replace",
    "read_tsv",
    "recursive_file_records",
    "validate_bc_only_s1_rows",
    "verify_recursive_checksums",
    "write_checkpoint_atomic",
    "write_recursive_checksums",
]
