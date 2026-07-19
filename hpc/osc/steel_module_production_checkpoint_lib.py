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
    CampaignTask,
    canonical_json,
    load_json,
    require_dict,
    require_sha256,
    require_string,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_program_lib import (
    ordered_task_hash,
    seed_pair_registry_hash,
    seed_set_hash,
    task_set_hash,
)
from steel_module_production_phase2b_lib import (
    EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1,
    EXECUTION_SCHEMA_VERSION_V1,
    EXECUTION_SCHEMA_VERSION_V2,
    FORMAL_EXECUTION_NAME,
    READINESS_LOCK_RELATIVE,
    SUCCESSOR_OBJECT_KIND,
    load_execution_companion,
    load_phase2a_lock,
)


CHECKPOINT_SCHEMA_VERSION = "steel-module-production-checkpoint-v1"
MANAGED_FINALIZATION_SCHEMA_VERSION = "steel-module-managed-finalization-v1"
RECOVERY_LINEAGE_SCHEMA_VERSION = "steel-module-managed-recovery-lineage-v1"
SUCCESSOR_AUTHORITY_SCHEMA_VERSION = (
    "steel-module-production-successor-authority-v1"
)
FORMAL_CHECKPOINT_STATE = "BC-ONLY-S1"
FORMAL_EVIDENCE_MODE = "back-center-only"
SUCCESSOR_FINALIZATION_BUNDLE_NAME = "whole-child"
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


def is_successor_execution_manifest(manifest: dict[str, Any]) -> bool:
    """Return whether a managed-execution manifest is the recovery successor.

    The schema and object-kind checks stay together so that a self-relabelled
    historical execution cannot be routed through the successor authority.
    """

    schema = manifest.get("schema_version")
    object_kind = manifest.get("object_kind")
    if schema == EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1:
        if object_kind != SUCCESSOR_OBJECT_KIND:
            raise ValueError("successor execution schema/object-kind mismatch")
        return True
    if schema in {EXECUTION_SCHEMA_VERSION_V1, EXECUTION_SCHEMA_VERSION_V2}:
        if object_kind == SUCCESSOR_OBJECT_KIND:
            raise ValueError("historical execution cannot claim successor object-kind")
        return False
    raise ValueError("unsupported managed-execution schema")


def managed_finalization_directory(
    execution_dir: Path, manifest: dict[str, Any]
) -> Path:
    """Return the schema-specific whole-child evidence directory.

    Historical execution schemas retain their original ``finalized/`` layout.
    The recovery successor keeps that pre-created directory as its writable
    publication container and atomically installs the evidence beneath the
    fixed ``whole-child`` leaf.  Keeping this routing in one helper prevents
    finalizer/checkpoint consumers from silently disagreeing about authority.
    """

    requested = execution_dir.expanduser()
    if requested.is_symlink():
        raise ValueError("managed execution must not be a symlink")
    directory = requested.resolve()
    finalized = directory / "finalized"
    if is_successor_execution_manifest(manifest):
        return finalized / SUCCESSOR_FINALIZATION_BUNDLE_NAME
    return finalized


def load_execution_for_downstream(
    execution_dir: Path,
    *,
    repo_root: Path | None,
    allow_test_mode: bool = False,
    require_readiness: bool = True,
    verify_runtime: bool = True,
    verify_live_predecessor: bool = True,
) -> Any:
    """Load old or recovery-successor execution with the correct authority.

    Importing the successor loader lazily avoids a module cycle: the successor
    implementation itself reuses this module's immutable evidence helpers.
    """

    requested = execution_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError("managed execution must not be a symlink")
    directory = requested.resolve()
    manifest_path = directory / "managed_execution.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("managed execution manifest is missing or unsafe")
    manifest = load_json(manifest_path)
    if is_successor_execution_manifest(manifest):
        from steel_module_production_successor_lib import load_successor_execution

        return load_successor_execution(
            directory,
            repo_root=repo_root,
            allow_test_mode=allow_test_mode,
            require_readiness=require_readiness,
            verify_runtime=verify_runtime,
            verify_live_predecessor=verify_live_predecessor,
        )
    return load_execution_companion(
        directory,
        repo_root=repo_root,
        allow_test_mode=allow_test_mode,
        require_readiness=require_readiness,
        verify_runtime=verify_runtime,
    )


def recovery_lineage_from_execution(execution: Any) -> dict[str, Any] | None:
    """Build immutable successor/predecessor provenance for downstream evidence."""

    manifest = execution.manifest
    if not is_successor_execution_manifest(manifest):
        return None
    authority = require_dict(manifest, "recovery_authority")
    incident = require_dict(authority, "incident")
    predecessor = require_dict(authority, "predecessor_execution")
    payload: dict[str, Any] = {
        "schema_version": RECOVERY_LINEAGE_SCHEMA_VERSION,
        "successor_execution": {
            "directory": str(execution.directory),
            "schema_version": manifest.get("schema_version"),
            "object_kind": manifest.get("object_kind"),
            "execution_generation": manifest.get("execution_generation"),
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
        },
        # Keep the complete authority record.  Compact incident fields alone
        # would lose the zero-consumption and exact retry-equivalence proof.
        "recovery_authority": json.loads(json.dumps(authority)),
        "predecessor_incident": {
            "directory": incident.get("directory"),
            "incident_id": incident.get("incident_id"),
            "incident_hash": incident.get("incident_hash"),
            "incident_json_sha256": incident.get("incident_json_sha256"),
            "checksum_manifest_sha256": incident.get(
                "checksum_manifest_sha256"
            ),
        },
        "predecessor_execution": {
            "directory": predecessor.get("directory"),
            "execution_id": predecessor.get("execution_id"),
            "execution_hash": predecessor.get("execution_hash"),
        },
        "result_scope": {
            "source_execution_directory": str(execution.directory),
            "selected_results": "successor-successes-only",
            "predecessor_failed_outputs_included": False,
            "predecessor_accounting_included": False,
        },
    }
    payload["lineage_hash"] = sha256_bytes(canonical_json(payload))
    return payload


def validate_recovery_lineage(
    execution_record: dict[str, Any], lineage: object
) -> dict[str, Any] | None:
    """Validate optional lineage, requiring it for every successor output."""

    schema = execution_record.get("schema_version")
    if schema != EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1:
        if lineage is not None:
            raise ValueError("historical execution must not carry recovery lineage")
        return None
    if not isinstance(lineage, dict):
        raise ValueError("successor evidence lacks recovery lineage")
    if lineage.get("schema_version") != RECOVERY_LINEAGE_SCHEMA_VERSION:
        raise ValueError("unsupported recovery-lineage schema")
    expected_hash = require_sha256(lineage, "lineage_hash")
    unhashed = dict(lineage)
    unhashed.pop("lineage_hash")
    if sha256_bytes(canonical_json(unhashed)) != expected_hash:
        raise ValueError("recovery-lineage semantic hash mismatch")
    successor = require_dict(lineage, "successor_execution")
    for key in (
        "directory",
        "schema_version",
        "object_kind",
        "execution_generation",
        "execution_id",
        "execution_hash",
    ):
        if successor.get(key) != execution_record.get(key):
            raise ValueError(f"recovery-lineage successor {key} mismatch")
    authority = require_dict(lineage, "recovery_authority")
    authority_hash = require_sha256(authority, "authority_hash")
    unhashed_authority = dict(authority)
    unhashed_authority.pop("authority_hash")
    if (
        authority.get("schema_version") != SUCCESSOR_AUTHORITY_SCHEMA_VERSION
        or sha256_bytes(canonical_json(unhashed_authority)) != authority_hash
        or authority_hash != execution_record.get("recovery_authority_hash")
    ):
        raise ValueError("recovery-lineage authority hash mismatch")
    zero = require_dict(authority, "zero_consumption")
    expected_zero = {
        "classification": "pre-simulation-control-plane-failure",
        "root_cause": "cross-node-control-lock-device-or-inode-mismatch",
        "accepted_incident_evidence": True,
        "events_consumed": 0,
        "production_seed_count": 64,
        "production_seeds_consumed": 0,
        "loader_completed": False,
        "apptainer_invoked": False,
        "geant4_invoked": False,
        "task_outputs_created": False,
        "root_outputs_created": False,
        "seed_reuse_authorized": True,
        "reuse_scope": "exact-32-task-phase2a-mapping-only",
    }
    if zero != expected_zero:
        raise ValueError("recovery-lineage does not prove exact zero consumption")
    incident = require_dict(authority, "incident")
    recorded_incident = require_dict(lineage, "predecessor_incident")
    for key in (
        "directory",
        "incident_id",
        "incident_hash",
        "incident_json_sha256",
        "checksum_manifest_sha256",
    ):
        if recorded_incident.get(key) != incident.get(key):
            raise ValueError(f"recovery-lineage incident {key} mismatch")
    predecessor = require_dict(authority, "predecessor_execution")
    recorded_predecessor = require_dict(lineage, "predecessor_execution")
    for key in ("directory", "execution_id", "execution_hash"):
        if recorded_predecessor.get(key) != predecessor.get(key):
            raise ValueError(f"recovery-lineage predecessor {key} mismatch")
    scope = require_dict(lineage, "result_scope")
    if scope != {
        "source_execution_directory": execution_record.get("directory"),
        "selected_results": "successor-successes-only",
        "predecessor_failed_outputs_included": False,
        "predecessor_accounting_included": False,
    }:
        raise ValueError("recovery-lineage result scope is unsafe")
    return lineage


def _tasks_from_finalized_rows(
    rows: Iterable[dict[str, str]],
) -> tuple[CampaignTask, ...]:
    return tuple(
        CampaignTask(
            task_index=_require_int(row, "task_index"),
            logical_task_id=row["logical_task_id"],
            stage=row["stage"],
            tile_thickness_mm=_require_int(row, "tile_thickness_mm"),
            sipm_layout=row["sipm_layout"],
            absorber_transverse_mm=_require_int(row, "absorber_transverse_mm"),
            x_mm=_require_int(row, "x_mm"),
            y_mm=_require_int(row, "y_mm"),
            seed_block=_require_int(row, "seed_block"),
            events=_require_int(row, "events"),
            seed1=_require_int(row, "seed1"),
            seed2=_require_int(row, "seed2"),
            configuration_hash=row["configuration_hash"],
        )
        for row in rows
    )


def _successor_task_identity(tasks: tuple[CampaignTask, ...]) -> dict[str, Any]:
    seeds = tuple(seed for task in tasks for seed in (task.seed1, task.seed2))
    return {
        "ordered_task_hash": ordered_task_hash(tasks),
        "task_set_hash": task_set_hash(tasks),
        "task_seed_mapping_hash": seed_pair_registry_hash(tasks),
        "seed_set_hash": seed_set_hash(seeds),
        "ordered_seed_pairs_sha256": sha256_bytes(
            canonical_json(
                [
                    {
                        "logical_task_id": task.logical_task_id,
                        "seed1": task.seed1,
                        "seed2": task.seed2,
                    }
                    for task in tasks
                ]
            )
        ),
        "ordered_production_seeds": list(seeds),
        "task_count": len(tasks),
        "event_count": sum(task.events for task in tasks),
        "seed_count": len(seeds),
    }


def validate_successor_seed_lineage(
    execution_record: dict[str, Any],
    lineage: dict[str, Any] | None,
    rows: Iterable[dict[str, str]],
    seed_audit: dict[str, Any],
    *,
    checkpoint_record: dict[str, Any] | None = None,
) -> None:
    """Recompute every successor task/seed identity from the selected rows."""

    if execution_record.get("schema_version") != EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1:
        return
    if lineage is None:
        raise ValueError("successor seed audit lacks recovery lineage")
    tasks = _tasks_from_finalized_rows(rows)
    identity = _successor_task_identity(tasks)
    authority = require_dict(lineage, "recovery_authority")
    retry = require_dict(authority, "retry_equivalence")
    for key, expected in identity.items():
        if retry.get(key) != expected:
            raise ValueError(f"successor retry-equivalence {key} mismatch")
    if retry.get("all_equal") is not True:
        raise ValueError("successor retry-equivalence is not accepted")
    for key in ("task_set_hash", "task_seed_mapping_hash", "seed_set_hash"):
        if execution_record.get(key) != identity[key]:
            raise ValueError(f"successor execution {key} mismatch")
    if checkpoint_record is not None:
        for checkpoint_key, identity_key in (
            ("task_set_hash", "task_set_hash"),
            ("task_seed_mapping_hash", "task_seed_mapping_hash"),
            ("production_seed_set_hash", "seed_set_hash"),
        ):
            if checkpoint_record.get(checkpoint_key) != identity[identity_key]:
                raise ValueError(f"checkpoint {checkpoint_key} mismatch")
    expected_seed_tasks = [
        {
            "logical_task_id": task.logical_task_id,
            "seed_block": task.seed_block,
            "seed1": task.seed1,
            "seed2": task.seed2,
        }
        for task in tasks
    ]
    expected_seed_fields = {
        "schema_version": "steel-module-managed-seed-audit-v1",
        "valid": True,
        "production_seed_count": 64,
        "production_unique_seed_count": 64,
        "production_seed_set_hash": identity["seed_set_hash"],
        "sealed_pilot_seed_count": 240,
        "sealed_pilot_unique_seed_count": 240,
        "overlap_count": 0,
        "overlap": [],
        "parent_registry_reconciled": True,
        "phase2a_plan_reconciled": True,
        "run_config_reconciled": True,
        "task_result_reconciled": True,
        "tasks": expected_seed_tasks,
    }
    for key, expected in expected_seed_fields.items():
        if seed_audit.get(key) != expected:
            raise ValueError(f"successor seed audit {key} mismatch")


def validate_successor_selection_and_accounting(
    directory: Path,
    validation: dict[str, Any],
    execution_record: dict[str, Any],
    lineage: dict[str, Any] | None,
    rows: tuple[dict[str, str], ...],
    selection: dict[str, Any],
) -> None:
    """Bind selected rows and every copied accounting snapshot to the successor."""

    if execution_record.get("schema_version") != EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1:
        return
    if lineage is None:
        raise ValueError("successor selection lacks recovery lineage")
    authority = require_dict(lineage, "recovery_authority")
    predecessor_attempt = require_dict(authority, "predecessor_attempt")
    forbidden_attempt = predecessor_attempt.get("attempt_id")
    forbidden_job = str(predecessor_attempt.get("job_id"))
    expected_selected = {
        row["logical_task_id"]: row["attempt_id"] for row in rows
    }
    explicit = selection.get("explicit_selection")
    if not isinstance(explicit, dict):
        raise ValueError("successor selection explicit map is invalid")
    if (
        selection.get("schema_version") != "steel-module-managed-selection-v1"
        or selection.get("selected_task_count") != 32
        or selection.get("source_execution_id") != execution_record.get("execution_id")
        or selection.get("source_execution_directory")
        != execution_record.get("directory")
        or selection.get("selected_results") != "successor-successes-only"
        or selection.get("predecessor_failed_outputs_included") is not False
        or selection.get("selected_attempts") != expected_selected
        or selection.get("duplicate_successes_resolved") != sorted(explicit)
    ):
        raise ValueError("successor selection record is inconsistent")
    selected_attempts = set(expected_selected.values())
    if forbidden_attempt in selected_attempts:
        raise ValueError("successor selection contains predecessor attempt")
    selected_jobs_by_attempt: dict[str, set[str]] = {}
    for row in rows:
        attempt_id = row["attempt_id"]
        job_id = row["slurm_job_id"]
        if job_id == forbidden_job:
            raise ValueError("successor selection contains predecessor scheduler job")
        selected_jobs_by_attempt.setdefault(attempt_id, set()).add(job_id)
    accounting_attempts = validation.get("accounting_attempts")
    if (
        not isinstance(accounting_attempts, list)
        or any(not isinstance(value, str) or not value for value in accounting_attempts)
        or len(set(accounting_attempts)) != len(accounting_attempts)
        or not selected_attempts.issubset(set(accounting_attempts))
        or forbidden_attempt in accounting_attempts
    ):
        raise ValueError("successor accounting-attempt registry is inconsistent")
    accounting_root = directory / "accounting"
    if accounting_root.is_symlink() or not accounting_root.is_dir():
        raise ValueError("successor finalization lacks accounting evidence")
    actual_attempts = {
        path.name
        for path in accounting_root.iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    if actual_attempts != set(accounting_attempts):
        raise ValueError("successor copied accounting set is inconsistent")
    for attempt_id in accounting_attempts:
        frozen = load_json(accounting_root / attempt_id / "frozen.json")
        job_id = str(frozen.get("job_id", ""))
        if frozen.get("attempt_id") != attempt_id or not job_id.isdigit():
            raise ValueError("successor copied accounting identity is invalid")
        if job_id == forbidden_job:
            raise ValueError("successor copied predecessor scheduler accounting")
        expected_jobs = selected_jobs_by_attempt.get(attempt_id)
        if expected_jobs is not None and expected_jobs != {job_id}:
            raise ValueError("successor selected rows/accounting job mismatch")


def readiness_identity_for_execution(
    execution: Any, *, repo_root: Path
) -> dict[str, Any]:
    """Return the readiness identity appropriate to this execution generation."""

    if execution.manifest.get("test_mode") is True:
        return {"required": False, "lock_sha256": None}
    if is_successor_execution_manifest(execution.manifest):
        from steel_module_production_successor_lib import (
            verify_successor_recovery_readiness,
        )

        path, value = verify_successor_recovery_readiness(
            execution, repo_root=repo_root
        )
        authority = require_dict(execution.manifest, "recovery_authority")
        incident = require_dict(authority, "incident")
        return {
            "required": True,
            "authority": "recovery-successor",
            "path": str(path),
            "schema_version": value.get("schema_version"),
            "lock_sha256": sha256_file(path),
            "readiness_hash": value.get("readiness_hash"),
            "recovery_authority_hash": authority.get("authority_hash"),
            "predecessor_incident_id": incident.get("incident_id"),
            "predecessor_incident_hash": incident.get("incident_hash"),
        }
    path = repo_root.resolve() / READINESS_LOCK_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise ValueError("formal finalization lacks a regular readiness lock")
    return {
        "required": True,
        "path": str(path),
        "lock_sha256": sha256_file(path),
    }


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
    execution_record = require_dict(validation, "execution")
    lineage = validate_recovery_lineage(
        execution_record, validation.get("recovery_lineage")
    )
    event_audit = load_json(directory / "event_audit.json")
    seed_audit = load_json(directory / "seed_audit.json")
    selection = load_json(directory / "selection_record.json")
    if lineage is not None:
        for name, value in (
            ("event_audit.json", event_audit),
            ("seed_audit.json", seed_audit),
            ("selection_record.json", selection),
        ):
            if value.get("recovery_lineage") != lineage:
                raise ValueError(f"managed finalization {name} lost recovery lineage")
        if (
            event_audit.get("execution_id") != execution_record.get("execution_id")
            or event_audit.get("execution_hash")
            != execution_record.get("execution_hash")
        ):
            raise ValueError("successor event audit execution identity mismatch")
    rows = validate_bc_only_s1_rows(
        read_tsv(directory / "task_index.tsv"),
        verify_root_files=verify_root_files,
    )
    validate_successor_seed_lineage(
        execution_record, lineage, rows, seed_audit
    )
    validate_successor_selection_and_accounting(
        directory,
        validation,
        execution_record,
        lineage,
        rows,
        selection,
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
    execution = require_dict(manifest, "execution")
    lineage = validate_recovery_lineage(
        execution, manifest.get("recovery_lineage")
    )
    if require_readiness and (
        readiness.get("required") is not True
        or not isinstance(readiness.get("lock_sha256"), str)
        or len(readiness["lock_sha256"]) != 64
    ):
        raise ValueError("checkpoint lacks the required Phase-2B readiness identity")
    if require_readiness and not test_mode:
        if repo_root is None:
            raise ValueError("formal checkpoint validation requires repo_root")
        _, phase2a = load_phase2a_lock(repo_root)
        canonical_child = Path(require_string(phase2a, "canonical_directory")).resolve()
        successor = execution.get("schema_version") == (
            EXECUTION_SCHEMA_VERSION_SUCCESSOR_V1
        )
        if successor:
            if execution.get("object_kind") != SUCCESSOR_OBJECT_KIND:
                raise ValueError("checkpoint successor schema/object-kind mismatch")
            from steel_module_production_successor_lib import (
                FORMAL_SUCCESSOR_EXECUTION_NAME,
            )

            execution_name = FORMAL_SUCCESSOR_EXECUTION_NAME
        else:
            execution_name = FORMAL_EXECUTION_NAME
        canonical_execution = canonical_child.parent / execution_name
        canonical_root = canonical_child.parent / "steel-module-production-checkpoints"
        if directory.parent != canonical_root:
            raise ValueError(f"formal checkpoint path must be under {canonical_root}")
        if execution.get("directory") != str(canonical_execution):
            raise ValueError("checkpoint execution directory is not canonical")
        loaded_execution = load_execution_for_downstream(
            canonical_execution,
            repo_root=repo_root,
            allow_test_mode=False,
            require_readiness=True,
            verify_runtime=True,
            verify_live_predecessor=True,
        )
        current_readiness = readiness_identity_for_execution(
            loaded_execution, repo_root=repo_root
        )
        if readiness != current_readiness:
            raise ValueError("checkpoint readiness identity is stale")
        for key, actual in (
            ("execution_id", loaded_execution.execution_id),
            ("execution_hash", loaded_execution.execution_hash),
        ):
            if execution.get(key) != actual:
                raise ValueError(f"checkpoint managed execution {key} mismatch")
        expected_lineage = recovery_lineage_from_execution(loaded_execution)
        if lineage != expected_lineage:
            raise ValueError("checkpoint recovery lineage changed")
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
    if lineage is not None and (
        event_audit.get("recovery_lineage") != lineage
        or seed_audit.get("recovery_lineage") != lineage
    ):
        raise ValueError("checkpoint audits lost recovery lineage")
    validate_successor_seed_lineage(
        execution,
        lineage,
        rows,
        seed_audit,
        checkpoint_record=manifest,
    )
    source = load_json(directory / "source_finalization.json")
    if source != require_dict(manifest, "source_finalization"):
        raise ValueError("checkpoint source-finalization record mismatch")
    if require_readiness and not test_mode:
        finalized = managed_finalization_directory(
            canonical_execution, loaded_execution.manifest
        )
        if source.get("directory") != str(finalized):
            raise ValueError("checkpoint source finalization is not canonical")
        validation, source_rows = load_managed_finalization(
            finalized,
            allow_test_mode=False,
            verify_root_files=verify_root_files,
        )
        if (
            require_dict(validation, "execution") != execution
            or validation.get("recovery_lineage") != lineage
        ):
            raise ValueError("checkpoint/source finalization lineage mismatch")
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
    if source_children.get("recovery_lineage") != lineage:
        raise ValueError("checkpoint source-children recovery lineage mismatch")
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
    "RECOVERY_LINEAGE_SCHEMA_VERSION",
    "SUCCESSOR_FINALIZATION_BUNDLE_NAME",
    "ProductionCheckpoint",
    "is_successor_execution_manifest",
    "load_execution_for_downstream",
    "load_managed_finalization",
    "load_production_checkpoint",
    "managed_finalization_directory",
    "publish_directory_no_replace",
    "read_tsv",
    "recursive_file_records",
    "readiness_identity_for_execution",
    "recovery_lineage_from_execution",
    "validate_bc_only_s1_rows",
    "validate_recovery_lineage",
    "verify_recursive_checksums",
    "write_checkpoint_atomic",
    "write_recursive_checksums",
]
