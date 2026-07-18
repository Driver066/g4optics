#!/usr/bin/env python3
"""Offline whole-child finalization for the managed BC-S1 execution companion."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from audit_realistic_neutron_campaign import SUMMARY_FLOAT_FIELDS, SUMMARY_INTEGER_FIELDS
from finalize_steel_module_campaign import CONFIGURATION_FIELDS, configuration_rows, write_csv
from realistic_neutron_event_audit import SIPM_SENSOR_FIELDS, read_and_audit_root
from record_realistic_neutron_task_result import integer_field, read_single_csv_row
from record_steel_module_task_result import validate_run_config
from steel_module_campaign_lib import CampaignTask, canonical_json, load_json, sha256_bytes, sha256_file
from steel_module_production_checkpoint_lib import (
    MANAGED_FINALIZATION_SCHEMA_VERSION,
    publish_directory_no_replace,
    write_recursive_checksums,
)
from steel_module_production_program_lib import load_production_program, seed_set_hash
from steel_module_production_phase2b_lib import (
    READINESS_LOCK_RELATIVE,
    attempt_state,
    base_slurm_state,
    execution_task_by_id,
    list_attempt_ids,
    load_attempt_intent,
    load_execution_companion,
    load_frozen_accounting,
    load_managed_task_result,
)


MANAGED_TASK_INDEX_FIELDS = (
    "task_index",
    "logical_task_id",
    "attempt_id",
    "slurm_job_id",
    "slurm_array_index",
    "stage",
    "tile_thickness_mm",
    "sipm_layout",
    "absorber_transverse_mm",
    "x_mm",
    "y_mm",
    "seed_block",
    "events",
    "seed1",
    "seed2",
    "configuration_hash",
    "task_result",
    "task_result_sha256",
    "run_config",
    "run_config_sha256",
    "macro",
    "macro_sha256",
    "simulation_log",
    "simulation_log_sha256",
    "root",
    "root_sha256",
    "summary",
    "summary_sha256",
    "efficiency_map",
    "efficiency_map_sha256",
)


@dataclass(frozen=True)
class ManagedCandidate:
    task: CampaignTask
    attempt: dict[str, Any]
    array_index: int
    job_id: str
    marker_path: Path
    marker: dict[str, Any]
    paths: dict[str, Path]
    summary: dict[str, str]
    event_report: dict[str, int | float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--execution-dir", required=True, type=Path)
    parser.add_argument(
        "--selection-file",
        type=Path,
        help="TSV with logical_task_id and attempt_id for duplicate successes.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Run the full offline audit without creating finalized evidence.",
    )
    return parser.parse_args()


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise ValueError(f"invalid SHA-256 identity for {label}")
    return value


def _resolve_execution_file(execution_dir: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"managed task artifact {label} has no path")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"managed task artifact {label} has an unsafe path")
    path = execution_dir / relative
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"managed task artifact {label} is not a regular file")
    resolved = path.resolve()
    try:
        resolved.relative_to(execution_dir)
    except ValueError as exc:
        raise ValueError(f"managed task artifact {label} escapes execution root") from exc
    return resolved


def _artifact_paths(execution: Any, marker: dict[str, Any]) -> dict[str, Path]:
    artifacts = marker.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("managed task marker artifacts must be an object")
    paths: dict[str, Path] = {}
    for key in (
        "run_config",
        "points",
        "macro",
        "simulation_log",
        "root",
        "summary",
        "efficiency_map",
    ):
        record = artifacts.get(key)
        if not isinstance(record, dict):
            raise ValueError(f"managed task marker lacks artifact {key}")
        path = _resolve_execution_file(execution.directory, record.get("path"), key)
        if path.stat().st_size != record.get("size_bytes"):
            raise ValueError(f"managed task artifact size changed: {key}")
        if sha256_file(path) != _require_digest(record.get("sha256"), key):
            raise ValueError(f"managed task artifact checksum mismatch: {key}")
        paths[key] = path
    return paths


def _intent_task_rows(execution: Any, attempt_id: str) -> tuple[dict[str, str], ...]:
    path = execution.directory / "intents" / attempt_id / "tasks.tsv"
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"attempt intent task map is missing: {attempt_id}")
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = tuple(reader)
    required = {"task_index", "logical_task_id"}
    if not required.issubset(reader.fieldnames or ()):
        raise ValueError(f"attempt intent has an invalid task map: {attempt_id}")
    logical_ids = [row["logical_task_id"] for row in rows]
    if any(not value for value in logical_ids) or len(set(logical_ids)) != len(rows):
        raise ValueError(f"attempt intent task map has duplicate IDs: {attempt_id}")
    return rows


def _accounting_rows(path: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw:
            continue
        values = raw.split("|")
        if values and values[-1] == "":
            values.pop()
        if len(values) < 3:
            raise ValueError(f"invalid frozen sacct row {number}: {raw!r}")
        rows.append(values)
    return rows


def _successful_array_indexes(
    execution: Any, attempt_id: str, accounting: dict[str, Any], job_id: str
) -> set[int]:
    if accounting.get("accepted_terminal") is not True:
        raise ValueError(f"attempt accounting is not accepted terminal: {attempt_id}")
    path = execution.directory / "attempts" / attempt_id / "accounting" / "sacct.psv"
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"attempt lacks frozen sacct.psv: {attempt_id}")
    rows = _accounting_rows(path)
    successful: set[int] = set()
    prefix = f"{job_id}_"
    for values in rows:
        raw_job_id = values[0]
        if not raw_job_id.startswith(prefix) or "." in raw_job_id:
            continue
        suffix = raw_job_id[len(prefix) :]
        if not suffix.isdigit():
            continue
        state = base_slurm_state(values[1])
        exit_code = values[2]
        if state == "COMPLETED" and exit_code == "0:0":
            successful.add(int(suffix))
    return successful


def _audit_candidate(
    execution: Any,
    task: CampaignTask,
    intent: dict[str, Any],
    *,
    array_index: int,
    job_id: str,
) -> ManagedCandidate:
    attempt_id = intent["attempt_id"]
    marker = load_managed_task_result(execution, attempt_id, task.logical_task_id)
    marker_path = (
        execution.directory
        / "attempts"
        / attempt_id
        / "tasks"
        / task.logical_task_id
        / "task_result.json"
    ).resolve()
    if marker_path.is_symlink() or not marker_path.is_file():
        raise ValueError("managed task-result marker is missing")
    paths = _artifact_paths(execution, marker)
    config = load_json(paths["run_config"])
    validate_run_config(config, task, attempt_id, execution.managed_child.plan)
    _, summary = read_single_csv_row(paths["summary"])
    if integer_field(summary, "events") != task.events:
        raise ValueError("task summary event count mismatch")
    if integer_field(summary, "committed_events") != task.events:
        raise ValueError("task summary committed-event count mismatch")
    _, event_report = read_and_audit_root(
        paths["root"], expected_events=task.events, context=task.logical_task_id
    )
    for field in SIPM_SENSOR_FIELDS:
        if field not in event_report:
            raise ValueError(f"event tree lacks per-sensor total: {field}")
    for field in SUMMARY_INTEGER_FIELDS:
        if int(summary[field]) != int(event_report[field]):
            raise ValueError(f"event audit disagrees with summary: {field}")
    for field in SUMMARY_FLOAT_FIELDS:
        if not math.isclose(
            float(summary[field]),
            float(event_report[field]),
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(f"event audit disagrees with summary: {field}")
    return ManagedCandidate(
        task=task,
        attempt=intent,
        array_index=array_index,
        job_id=job_id,
        marker_path=marker_path,
        marker=marker,
        paths=paths,
        summary=summary,
        event_report=event_report,
    )


def collect_candidates(
    execution: Any,
) -> tuple[dict[str, list[ManagedCandidate]], list[dict[str, str]], list[str]]:
    known = execution_task_by_id(execution)
    candidates: dict[str, list[ManagedCandidate]] = defaultdict(list)
    invalid: list[dict[str, str]] = []
    accounting_attempts: list[str] = []
    intent_ids = list_attempt_ids(execution)
    for attempt_id in intent_ids:
        intent = load_attempt_intent(execution, attempt_id)
        state = attempt_state(execution, attempt_id)
        if state.unresolved:
            raise ValueError(f"attempt remains unresolved: {attempt_id} ({state.status})")
        if not state.scheduler_contacted:
            if state.status not in ("prepared", "cancelled"):
                raise ValueError(f"unexpected no-scheduler attempt state: {state.status}")
            continue
        if state.status != "terminal-accounting-frozen" or not state.terminal:
            raise ValueError(f"scheduler-contacted attempt is not terminal: {attempt_id}")
        accounting = load_frozen_accounting(execution, attempt_id)
        job_id = str(accounting.get("job_id", state.job_id or ""))
        if not job_id.isdigit() or (state.job_id is not None and job_id != state.job_id):
            raise ValueError(f"frozen accounting job ID mismatch: {attempt_id}")
        successful = _successful_array_indexes(execution, attempt_id, accounting, job_id)
        task_rows = _intent_task_rows(execution, attempt_id)
        selected = intent.get("selected")
        if not isinstance(selected, dict):
            raise ValueError(f"attempt intent selected record is invalid: {attempt_id}")
        logical_ids = selected.get("logical_task_ids")
        if logical_ids != [row["logical_task_id"] for row in task_rows]:
            raise ValueError(f"attempt intent logical-task order mismatch: {attempt_id}")
        accounting_attempts.append(attempt_id)
        for array_index, row in enumerate(task_rows, 1):
            if array_index not in successful:
                continue
            logical_id = row["logical_task_id"]
            task = known.get(logical_id)
            if task is None or int(row["task_index"]) != task.task_index:
                raise ValueError(f"attempt contains an unknown task: {logical_id}")
            try:
                candidates[logical_id].append(
                    _audit_candidate(
                        execution,
                        task,
                        intent,
                        array_index=array_index,
                        job_id=job_id,
                    )
                )
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                invalid.append(
                    {
                        "logical_task_id": logical_id,
                        "attempt_id": attempt_id,
                        "reason": str(exc),
                    }
                )
    return candidates, invalid, accounting_attempts


def load_selection(path: Path | None) -> tuple[dict[str, str], str | None]:
    if path is None:
        return {}, None
    source = path.expanduser()
    if source.is_symlink() or not source.is_file():
        raise ValueError("selection file must be a regular file")
    source = source.resolve()
    with source.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
    if list(reader.fieldnames or ()) != ["logical_task_id", "attempt_id"]:
        raise ValueError("selection TSV header must be logical_task_id, attempt_id")
    selection: dict[str, str] = {}
    for row in rows:
        logical_id = row["logical_task_id"]
        attempt_id = row["attempt_id"]
        if not logical_id or not attempt_id or logical_id in selection:
            raise ValueError("selection TSV contains empty or duplicate identities")
        selection[logical_id] = attempt_id
    return selection, sha256_file(source)


def select_candidates(
    execution: Any,
    candidates: dict[str, list[ManagedCandidate]],
    selection: dict[str, str],
) -> list[ManagedCandidate]:
    known = execution_task_by_id(execution)
    unknown = set(selection) - set(known)
    if unknown:
        raise ValueError(f"selection contains unknown tasks: {sorted(unknown)}")
    selected: list[ManagedCandidate] = []
    for task in execution.tasks:
        valid = candidates.get(task.logical_task_id, [])
        requested = selection.get(task.logical_task_id)
        if requested is not None:
            matches = [item for item in valid if item.attempt["attempt_id"] == requested]
            if len(matches) != 1:
                raise ValueError(
                    f"selection is not one valid success for {task.logical_task_id}"
                )
            selected.append(matches[0])
        elif len(valid) == 1:
            selected.append(valid[0])
        elif not valid:
            raise ValueError(f"no valid successful result for {task.logical_task_id}")
        else:
            raise ValueError(
                f"duplicate successful results for {task.logical_task_id}; "
                "provide --selection-file"
            )
    if len(selected) != 32 or sum(item.task.events for item in selected) != 8_000:
        raise ValueError("selected evidence is not the complete 32-task BC-S1 child")
    return selected


def _task_index_rows(selected: Iterable[ManagedCandidate]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in selected:
        task = item.task
        artifacts = item.marker["artifacts"]
        row: dict[str, object] = {
            **asdict(task),
            "attempt_id": item.attempt["attempt_id"],
            "slurm_job_id": item.job_id,
            "slurm_array_index": item.array_index,
            "task_result": str(item.marker_path),
            "task_result_sha256": sha256_file(item.marker_path),
        }
        for key in (
            "run_config",
            "macro",
            "simulation_log",
            "root",
            "summary",
            "efficiency_map",
        ):
            row[key] = str(item.paths[key])
            row[f"{key}_sha256"] = artifacts[key]["sha256"]
        rows.append(row)
    return rows


def _write_tsv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _legacy_candidates(selected: Iterable[ManagedCandidate]) -> list[Any]:
    # configuration_rows is intentionally duck-typed: it uses only task and
    # event_report.  Keeping the existing implementation prevents metric drift.
    return list(selected)


def _program_and_seed_audit(execution: Any, selected: list[ManagedCandidate]) -> tuple[dict[str, Any], dict[str, Any]]:
    binding_program = execution.managed_child.binding.get("program")
    if not isinstance(binding_program, dict):
        raise ValueError("managed child binding lacks program identity")
    program_dir = binding_program.get("program_directory")
    if not isinstance(program_dir, str):
        raise ValueError("managed child binding lacks program directory")
    program = load_production_program(Path(program_dir), verify_runtime_artifacts=False)
    parent_tasks = program.child_tasks.get("BC-S1")
    if parent_tasks is None or tuple(item.task for item in selected) != parent_tasks:
        raise ValueError("selected task order does not reproduce parent BC-S1 registry")
    production_seeds = [
        seed for item in selected for seed in (item.task.seed1, item.task.seed2)
    ]
    pilot_seeds = [record.seed for record in program.excluded_seeds]
    overlap = sorted(set(production_seeds) & set(pilot_seeds))
    if (
        len(production_seeds) != 64
        or len(set(production_seeds)) != 64
        or len(pilot_seeds) != 240
        or len(set(pilot_seeds)) != 240
        or overlap
    ):
        raise ValueError("production/pilot seed separation audit failed")
    audit = {
        "schema_version": "steel-module-managed-seed-audit-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "valid": True,
        "production_seed_count": 64,
        "production_unique_seed_count": 64,
        "production_seed_set_hash": seed_set_hash(production_seeds),
        "sealed_pilot_seed_count": 240,
        "sealed_pilot_unique_seed_count": 240,
        "sealed_pilot_seed_set_hash": seed_set_hash(pilot_seeds),
        "overlap_count": 0,
        "overlap": overlap,
        "parent_registry_reconciled": True,
        "phase2a_plan_reconciled": True,
        "run_config_reconciled": True,
        "task_result_reconciled": True,
        "tasks": [
            {
                "logical_task_id": item.task.logical_task_id,
                "seed_block": item.task.seed_block,
                "seed1": item.task.seed1,
                "seed2": item.task.seed2,
            }
            for item in selected
        ],
    }
    program_identity = {
        "program_directory": str(program.directory),
        "program_id": program.manifest.get("program_id"),
        "program_hash": program.manifest.get("program_hash"),
        "parent_plan_hash": binding_program.get("parent_plan_hash"),
        "authorization_graph_hash": binding_program.get("authorization_graph_hash"),
        "production_program_json_sha256": sha256_file(
            program.directory / "production_program.json"
        ),
        "root_checksum_manifest_sha256": sha256_file(
            program.directory / "SHA256SUMS"
        ),
    }
    return program_identity, audit


def _copy_accounting(execution: Any, target: Path, attempt_ids: Iterable[str]) -> None:
    root = target / "accounting"
    root.mkdir()
    for attempt_id in sorted(set(attempt_ids)):
        source = execution.directory / "attempts" / attempt_id / "accounting"
        if source.is_symlink() or not source.is_dir():
            raise ValueError(f"missing regular accounting directory: {attempt_id}")
        destination = root / attempt_id
        shutil.copytree(source, destination, symlinks=False)
        if any(path.is_symlink() for path in destination.rglob("*")):
            raise ValueError("accounting snapshot contains a symlink")


def finalize(
    execution: Any,
    repo_root: Path,
    output_dir: Path,
    selected: list[ManagedCandidate],
    invalid: list[dict[str, str]],
    accounting_attempts: list[str],
    *,
    selection: dict[str, str],
    selection_file_sha256: str | None,
) -> None:
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError(f"refusing to overwrite finalization directory: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        task_rows = _task_index_rows(selected)
        _write_tsv(temp / "task_index.tsv", MANAGED_TASK_INDEX_FIELDS, task_rows)
        write_csv(
            temp / "configuration_summary.csv",
            CONFIGURATION_FIELDS,
            configuration_rows(_legacy_candidates(selected)),
        )
        event_audit: dict[str, Any] = {
            "schema_version": "steel-module-managed-event-audit-v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "valid": True,
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
            "task_count": 32,
            "event_count": 8_000,
            "accepted_statistical_evidence": execution.manifest.get(
                "accepted_statistical_evidence"
            ),
            "tasks": [
                {
                    "logical_task_id": item.task.logical_task_id,
                    "tile_thickness_mm": item.task.tile_thickness_mm,
                    "sipm_layout": item.task.sipm_layout,
                    "seed_block": item.task.seed_block,
                    "events": item.task.events,
                    "root": str(item.paths["root"]),
                    "root_sha256": item.marker["artifacts"]["root"]["sha256"],
                    "audit": item.event_report,
                }
                for item in selected
            ],
        }
        program_identity, seed_audit = _program_and_seed_audit(execution, selected)
        for name, value in (
            ("event_audit.json", event_audit),
            ("seed_audit.json", seed_audit),
        ):
            (temp / name).write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        selection_record = {
            "schema_version": "steel-module-managed-selection-v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "selection_file_sha256": selection_file_sha256,
            "explicit_selection": selection,
            "selected_attempts": {
                item.task.logical_task_id: item.attempt["attempt_id"]
                for item in selected
            },
            "selected_task_count": 32,
            "duplicate_successes_resolved": sorted(selection),
        }
        (temp / "selection_record.json").write_text(
            json.dumps(selection_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _copy_accounting(execution, temp, accounting_attempts)

        sealed_pilot = execution.manifest.get("sealed_pilot")
        if not isinstance(sealed_pilot, dict):
            raise ValueError("execution manifest lacks the sealed-pilot identity")
        pilot_directory = sealed_pilot.get("directory")
        if not isinstance(pilot_directory, str):
            raise ValueError("sealed-pilot directory identity is invalid")
        pilot_baseline = {
            "campaign_directory": pilot_directory,
            "campaign_id": sealed_pilot.get("campaign_id"),
            "plan_hash": sealed_pilot.get("plan_hash"),
            "simulation_commit": sealed_pilot.get("simulation_commit"),
            "finalized_sha256s_sha256": sealed_pilot.get(
                "finalized_checksums_sha256"
            ),
            "analysis_v2_directory": str(
                Path(pilot_directory) / "finalized" / "analysis-v2"
            ),
            "analysis_v2_sha256s_sha256": sealed_pilot.get(
                "analysis_v2_checksums_sha256"
            ),
            "analysis_v2_config_sha256": sealed_pilot.get(
                "analysis_v2_config_sha256"
            ),
            "analysis_commit": sealed_pilot.get("analysis_commit"),
        }
        if execution.manifest.get("test_mode") is True:
            readiness = {"required": False, "lock_sha256": None}
        else:
            readiness_path = repo_root.resolve() / READINESS_LOCK_RELATIVE
            if readiness_path.is_symlink() or not readiness_path.is_file():
                raise ValueError("formal finalization lacks a regular readiness lock")
            readiness = {
                "required": True,
                "path": str(readiness_path),
                "lock_sha256": sha256_file(readiness_path),
            }
        execution_identity = {
            "directory": str(execution.directory),
            "execution_id": execution.execution_id,
            "execution_hash": execution.execution_hash,
            "campaign_id": execution.campaign_id,
            "plan_hash": execution.plan_hash,
            "simulation_commit": execution.git_commit,
            "child_id": "BC-S1",
            "child_plan_hash": execution.managed_child.binding["child"][
                "child_plan_hash"
            ],
            "task_set_hash": execution.managed_child.binding["child"][
                "task_set_hash"
            ],
            "parent_task_plan_hash": execution.managed_child.binding["child"][
                "parent_task_plan_hash"
            ],
            "task_seed_mapping_hash": execution.managed_child.binding["child"][
                "task_seed_mapping_hash"
            ],
            "seed_set_hash": execution.managed_child.binding["child"][
                "seed_set_hash"
            ],
            "binding_hash": execution.managed_child.binding["binding_hash"],
        }
        stable_identity = {
            "execution": execution_identity,
            "program": program_identity,
            "readiness": readiness,
            "pilot_baseline": pilot_baseline,
            "task_index_sha256": sha256_file(temp / "task_index.tsv"),
            "event_audit_sha256": sha256_file(temp / "event_audit.json"),
            "seed_audit_sha256": sha256_file(temp / "seed_audit.json"),
            "selection_record_sha256": sha256_file(temp / "selection_record.json"),
        }
        finalization_hash = sha256_bytes(canonical_json(stable_identity))
        test_mode = execution.manifest.get("test_mode") is True
        accepted = execution.manifest.get("accepted_statistical_evidence") is True
        if accepted == test_mode:
            raise ValueError("execution acceptance/test-mode flags disagree")
        validation = {
            "schema_version": MANAGED_FINALIZATION_SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "valid": True,
            "finalization_hash": finalization_hash,
            "accepted_statistical_evidence": accepted,
            "test_mode": test_mode,
            "child_id": "BC-S1",
            "task_count": 32,
            "event_count": 8_000,
            "event_audit_integrated": True,
            "seed_audit_integrated": True,
            "all_scheduler_contacted_attempts_terminal": True,
            "accounting_attempts": sorted(set(accounting_attempts)),
            "invalid_attempt_results_ignored": invalid,
            "program": program_identity,
            "execution": execution_identity,
            "readiness": readiness,
            "pilot_baseline": pilot_baseline,
            "identity": stable_identity,
        }
        (temp / "validation_report.json").write_text(
            json.dumps(validation, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_recursive_checksums(temp)
        publish_directory_no_replace(temp, output_dir)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        execution = load_execution_companion(
            args.execution_dir,
            repo_root=repo_root,
            allow_test_mode=False,
            require_readiness=True,
            verify_runtime=True,
        )
        candidates, invalid, accounting_attempts = collect_candidates(execution)
        selection, selection_sha = load_selection(args.selection_file)
        selected = select_candidates(execution, candidates, selection)
        output = execution.directory / "finalized"
        if args.check_only:
            print("managed steel-module child finalization check: PASS")
            print("child: BC-S1")
            print(f"tasks: {len(selected)}")
            print(f"events: {sum(item.task.events for item in selected)}")
            print("finalization written: false")
            return 0
        finalize(
            execution,
            repo_root,
            output,
            selected,
            invalid,
            accounting_attempts,
            selection=selection,
            selection_file_sha256=selection_sha,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot finalize managed steel-module child: {exc}", file=sys.stderr)
        return 1
    print("managed steel-module child finalization: PASS")
    print(f"child: BC-S1")
    print(f"tasks: {len(selected)}")
    print(f"events: {sum(item.task.events for item in selected)}")
    print(f"output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
