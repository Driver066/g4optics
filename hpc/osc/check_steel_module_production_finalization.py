#!/usr/bin/env python3
"""Focused synthetic checks for managed finalization/checkpoint evidence."""

from __future__ import annotations

import csv
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import finalize_steel_module_managed_child as finalizer
import steel_module_production_checkpoint_lib as checkpoint_lib
from build_steel_module_production_checkpoint import build_checkpoint
from steel_module_campaign_lib import (
    CampaignTask,
    canonical_json,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_checkpoint_lib import (
    MANAGED_FINALIZATION_SCHEMA_VERSION,
    load_managed_finalization,
    load_production_checkpoint,
    validate_bc_only_s1_rows,
    verify_recursive_checksums,
    write_checkpoint_atomic,
    write_recursive_checksums,
)


HASH = "a" * 64


def expect_failure(function: Any, *args: Any, contains: str | None = None, **kwargs: Any) -> None:
    try:
        function(*args, **kwargs)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if contains is not None and contains not in str(exc):
            raise AssertionError(f"unexpected failure text: {exc}") from exc
    else:
        raise AssertionError("operation unexpectedly succeeded")


def json_write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def rewrite_recursive_checksums(directory: Path) -> None:
    (directory / "SHA256SUMS").unlink()
    write_recursive_checksums(directory)


def reseal_checkpoint(directory: Path) -> Path:
    """Recompute artifact identities and the semantic checkpoint hash."""

    manifest_path = directory / "checkpoint.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["task_index_sha256"] = sha256_file(directory / "task_index.tsv")
    manifest["event_audit_sha256"] = sha256_file(directory / "event_audit.json")
    manifest["seed_audit_sha256"] = sha256_file(directory / "seed_audit.json")
    manifest["source_children_sha256"] = sha256_file(
        directory / "source_children.json"
    )
    manifest["source_finalization_sha256"] = sha256_file(
        directory / "source_finalization.json"
    )
    manifest.pop("checkpoint_hash", None)
    manifest["checkpoint_hash"] = sha256_bytes(canonical_json(manifest))
    json_write(manifest_path, manifest)
    target = directory.with_name(
        f"bc-only-s1-{manifest['checkpoint_hash'][:12]}"
    )
    if target != directory:
        if target.exists():
            shutil.rmtree(target)
        directory.rename(target)
        directory = target
    rewrite_recursive_checksums(directory)
    return directory


def concurrent_publish_worker(
    output_root: str,
    barrier: Any,
    queue: Any,
) -> None:
    """Two processes deliberately race the same content-addressed publish."""

    try:
        barrier.wait(timeout=10)
        target = write_checkpoint_atomic(
            Path(output_root),
            manifest_payload={"schema_version": "concurrent-fixture-v1"},
            task_index_text="task_index\n1\n",
            configuration_summary_text="payload\n" + ("x" * 2_000_000),
            event_audit={"valid": True},
            seed_audit={"valid": True},
            source_children={"children": []},
            source_finalization={"fixture": True},
        )
        queue.put(("success", str(target)))
    except BaseException as exc:  # pragma: no cover - child process transport
        queue.put(("failure", f"{type(exc).__name__}: {exc}"))


def fixture_rows(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    task_index = 0
    seed = 1000
    for thickness in (4, 24):
        for block in range(16):
            task_index += 1
            logical_id = f"production-t{thickness:02d}-a500-back-center-b{block:02d}"
            fake_root = root / "unmaterialized-root" / f"{logical_id}.root"
            rows.append(
                {
                    "task_index": task_index,
                    "logical_task_id": logical_id,
                    "attempt_id": "fixture-attempt",
                    "slurm_job_id": "12345",
                    "slurm_array_index": task_index,
                    "stage": "production",
                    "tile_thickness_mm": thickness,
                    "sipm_layout": "back-center",
                    "absorber_transverse_mm": 500,
                    "x_mm": 0,
                    "y_mm": 0,
                    "seed_block": block,
                    "events": 250,
                    "seed1": seed,
                    "seed2": seed + 1,
                    "configuration_hash": HASH,
                    "task_result": str(root / "task_result.json"),
                    "task_result_sha256": HASH,
                    "run_config": str(root / "run_config.json"),
                    "run_config_sha256": HASH,
                    "macro": str(root / "run.mac"),
                    "macro_sha256": HASH,
                    "simulation_log": str(root / "simulation.log"),
                    "simulation_log_sha256": HASH,
                    "root": str(fake_root),
                    "root_sha256": HASH,
                    "summary": str(root / "summary.csv"),
                    "summary_sha256": HASH,
                    "efficiency_map": str(root / "efficiency_map.csv"),
                    "efficiency_map_sha256": HASH,
                }
            )
            seed += 2
    return rows


def write_finalization(root: Path) -> Path:
    finalization = root / "execution" / "finalized"
    finalization.mkdir(parents=True)
    rows = fixture_rows(root)
    with (finalization / "task_index.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    (finalization / "configuration_summary.csv").write_text(
        "stage,tile_thickness_mm\n", encoding="utf-8"
    )
    event_tasks = [
        {
            "logical_task_id": row["logical_task_id"],
            "tile_thickness_mm": row["tile_thickness_mm"],
            "sipm_layout": "back-center",
            "seed_block": row["seed_block"],
            "events": 250,
            "root": row["root"],
            "root_sha256": HASH,
            "audit": {"events": 250},
        }
        for row in rows
    ]
    json_write(
        finalization / "event_audit.json",
        {
            "schema_version": "steel-module-managed-event-audit-v1",
            "valid": True,
            "task_count": 32,
            "event_count": 8000,
            "tasks": event_tasks,
        },
    )
    json_write(
        finalization / "seed_audit.json",
        {
            "schema_version": "steel-module-managed-seed-audit-v1",
            "valid": True,
            "production_seed_count": 64,
            "production_unique_seed_count": 64,
            "sealed_pilot_seed_count": 240,
            "sealed_pilot_unique_seed_count": 240,
            "overlap_count": 0,
        },
    )
    json_write(
        finalization / "selection_record.json",
        {
            "schema_version": "steel-module-managed-selection-v1",
            "selected_task_count": 32,
        },
    )
    readiness = {"required": True, "lock_sha256": "b" * 64}
    pilot = {
        "campaign_directory": "/fixture/pilot",
        "finalized_sha256s_sha256": "c" * 64,
        "analysis_v2_directory": "/fixture/pilot/finalized/analysis-v2",
        "analysis_v2_sha256s_sha256": "d" * 64,
    }
    json_write(
        finalization / "validation_report.json",
        {
            "schema_version": MANAGED_FINALIZATION_SCHEMA_VERSION,
            "valid": True,
            "finalization_hash": "e" * 64,
            "accepted_statistical_evidence": False,
            "test_mode": True,
            "child_id": "BC-S1",
            "task_count": 32,
            "event_count": 8000,
            "event_audit_integrated": True,
            "seed_audit_integrated": True,
            "program": {
                "program_id": "fixture-program",
                "program_hash": "f" * 64,
            },
            "execution": {
                "execution_id": "fixture-execution",
                "execution_hash": "1" * 64,
            },
            "readiness": readiness,
            "pilot_baseline": pilot,
        },
    )
    write_recursive_checksums(finalization)
    return finalization


def check_candidate_selection(root: Path) -> None:
    tasks = tuple(
        CampaignTask(
            task_index=index,
            logical_task_id=str(row["logical_task_id"]),
            stage="production",
            tile_thickness_mm=int(row["tile_thickness_mm"]),
            sipm_layout="back-center",
            absorber_transverse_mm=500,
            x_mm=0,
            y_mm=0,
            seed_block=int(row["seed_block"]),
            events=250,
            seed1=int(row["seed1"]),
            seed2=int(row["seed2"]),
            configuration_hash=HASH,
        )
        for index, row in enumerate(fixture_rows(root), 1)
    )
    execution = SimpleNamespace(tasks=tasks)

    def candidate(task: CampaignTask, attempt_id: str) -> finalizer.ManagedCandidate:
        return finalizer.ManagedCandidate(
            task=task,
            attempt={"attempt_id": attempt_id},
            array_index=task.task_index,
            job_id="12345",
            marker_path=root / "task_result.json",
            marker={},
            paths={},
            summary={},
            event_report={},
        )

    candidates = {task.logical_task_id: [candidate(task, "attempt-one")] for task in tasks}
    assert len(finalizer.select_candidates(execution, candidates, {})) == 32
    first = tasks[0].logical_task_id
    candidates[first].append(candidate(tasks[0], "attempt-two"))
    expect_failure(
        finalizer.select_candidates,
        execution,
        candidates,
        {},
        contains="duplicate successful",
    )
    selected = finalizer.select_candidates(
        execution, candidates, {first: "attempt-two"}
    )
    assert selected[0].attempt["attempt_id"] == "attempt-two"
    expect_failure(
        finalizer.select_candidates,
        execution,
        candidates,
        {first: "missing-attempt"},
        contains="not one valid success",
    )

    selection_path = root / "selection.tsv"
    selection_path.write_text(
        "logical_task_id\tattempt_id\n"
        f"{first}\tattempt-two\n",
        encoding="utf-8",
    )
    parsed, selection_sha = finalizer.load_selection(selection_path)
    assert parsed == {first: "attempt-two"}
    assert selection_sha == sha256_file(selection_path)
    duplicate_selection = root / "duplicate-selection.tsv"
    duplicate_selection.write_text(
        "logical_task_id\tattempt_id\n"
        f"{first}\tattempt-one\n"
        f"{first}\tattempt-two\n",
        encoding="utf-8",
    )
    expect_failure(
        finalizer.load_selection,
        duplicate_selection,
        contains="empty or duplicate",
    )

    accounting = root / "sacct.psv"
    accounting.write_text(
        "12345_1|COMPLETED|0:0|9||\n"
        "12345_1.batch|COMPLETED|0:0|9|180000K|0\n",
        encoding="utf-8",
    )
    rows = finalizer._accounting_rows(accounting)
    assert rows[0][:3] == ["12345_1", "COMPLETED", "0:0"]


def check_recursive_tamper(checkpoint_dir: Path, scratch: Path) -> None:
    digest_case = scratch / "checksum-digest-tamper"
    shutil.copytree(checkpoint_dir, digest_case)
    (digest_case / "event_audit.json").write_text("{}\n", encoding="utf-8")
    expect_failure(
        verify_recursive_checksums,
        digest_case,
        contains="checksum mismatch",
    )

    path_case = scratch / "checksum-path-tamper"
    shutil.copytree(checkpoint_dir, path_case)
    lines = (path_case / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    digest, _ = lines[0].split("  ", 1)
    lines[0] = f"{digest}  ../escape"
    os.chmod(path_case / "SHA256SUMS", 0o644)
    (path_case / "SHA256SUMS").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    expect_failure(
        verify_recursive_checksums,
        path_case,
        contains="unsafe checksum-manifest path",
    )

    symlink_case = scratch / "checksum-symlink-tamper"
    shutil.copytree(checkpoint_dir, symlink_case)
    (symlink_case / "symlink.json").symlink_to(
        symlink_case / "event_audit.json"
    )
    expect_failure(
        verify_recursive_checksums,
        symlink_case,
        contains="non-regular evidence file",
    )


def check_checkpoint_semantics(
    checkpoint_dir: Path,
    rows: tuple[dict[str, str], ...],
    scratch: Path,
) -> None:
    gap = [dict(row) for row in rows[:-1]]
    expect_failure(
        validate_bc_only_s1_rows,
        gap,
        verify_root_files=False,
        contains="exactly 32 tasks",
    )

    overlap = [dict(row) for row in rows]
    overlap[-1]["seed_block"] = "14"
    expect_failure(
        validate_bc_only_s1_rows,
        overlap,
        verify_root_files=False,
        contains="duplicate",
    )

    fixed = [dict(row) for row in rows]
    fixed[0]["logical_task_id"] = "fixed-t04-back-center-b00"
    fixed[0]["stage"] = "FIXED"
    expect_failure(
        validate_bc_only_s1_rows,
        fixed,
        verify_root_files=False,
        contains="outside exact",
    )

    wrong_prefix_parent = scratch / "wrong-prefix-parent"
    wrong_prefix_parent.mkdir()
    wrong_prefix = wrong_prefix_parent / f"wrong-prefix-{checkpoint_dir.name}"
    shutil.copytree(checkpoint_dir, wrong_prefix)
    expect_failure(
        load_production_checkpoint,
        wrong_prefix,
        allow_test_mode=True,
        verify_root_files=False,
        contains="directory name",
    )

    wrong_state_parent = scratch / "wrong-state-parent"
    wrong_state_parent.mkdir()
    wrong_state = wrong_state_parent / checkpoint_dir.name
    shutil.copytree(checkpoint_dir, wrong_state)
    manifest = json.loads((wrong_state / "checkpoint.json").read_text(encoding="utf-8"))
    manifest["state_id"] = "FIXED"
    json_write(wrong_state / "checkpoint.json", manifest)
    wrong_state = reseal_checkpoint(wrong_state)
    expect_failure(
        load_production_checkpoint,
        wrong_state,
        allow_test_mode=True,
        verify_root_files=False,
        contains="not exact BC-ONLY-S1",
    )


def check_source_readiness_and_seed_identity(
    checkpoint_dir: Path, scratch: Path
) -> None:
    source_parent = scratch / "source-identity-parent"
    source_parent.mkdir()
    source_case = source_parent / checkpoint_dir.name
    shutil.copytree(checkpoint_dir, source_case)
    source = json.loads(
        (source_case / "source_finalization.json").read_text(encoding="utf-8")
    )
    source["directory"] = "/tampered/finalization"
    json_write(source_case / "source_finalization.json", source)
    rewrite_recursive_checksums(source_case)
    expect_failure(
        load_production_checkpoint,
        source_case,
        allow_test_mode=True,
        verify_root_files=False,
        contains="source-finalization record mismatch",
    )

    readiness_parent = scratch / "readiness-identity-parent"
    readiness_parent.mkdir()
    readiness_case = readiness_parent / checkpoint_dir.name
    shutil.copytree(checkpoint_dir, readiness_case)
    manifest = json.loads(
        (readiness_case / "checkpoint.json").read_text(encoding="utf-8")
    )
    manifest["readiness"] = {"required": False, "lock_sha256": None}
    json_write(readiness_case / "checkpoint.json", manifest)
    readiness_case = reseal_checkpoint(readiness_case)
    expect_failure(
        load_production_checkpoint,
        readiness_case,
        allow_test_mode=True,
        require_readiness=True,
        verify_root_files=False,
        contains="lacks the required Phase-2B readiness identity",
    )

    seed_parent = scratch / "seed-audit-parent"
    seed_parent.mkdir()
    seed_case = seed_parent / checkpoint_dir.name
    shutil.copytree(checkpoint_dir, seed_case)
    seed_audit = json.loads(
        (seed_case / "seed_audit.json").read_text(encoding="utf-8")
    )
    seed_audit["production_seed_count"] = 63
    seed_audit["overlap_count"] = 1
    json_write(seed_case / "seed_audit.json", seed_audit)
    seed_case = reseal_checkpoint(seed_case)
    expect_failure(
        load_production_checkpoint,
        seed_case,
        allow_test_mode=True,
        verify_root_files=False,
        contains="seed audit is invalid",
    )


def check_publish_safety(scratch: Path) -> None:
    failure_root = scratch / "failure-cleanup"
    failure_root.mkdir()
    with patch.object(
        checkpoint_lib,
        "publish_directory_no_replace",
        side_effect=OSError("injected publish failure"),
    ):
        expect_failure(
            write_checkpoint_atomic,
            failure_root,
            manifest_payload={"schema_version": "failure-fixture-v1"},
            task_index_text="task_index\n1\n",
            configuration_summary_text="fixture\n",
            event_audit={"valid": True},
            seed_audit={"valid": True},
            source_children={"children": []},
            source_finalization={"fixture": True},
            contains="injected publish failure",
        )
    assert not list(failure_root.glob(".bc-only-s1.tmp-*"))
    assert not list(failure_root.glob("bc-only-s1-*"))

    concurrency_root = scratch / "concurrent-publish"
    concurrency_root.mkdir()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(
            target=concurrent_publish_worker,
            args=(str(concurrency_root), barrier, queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join()
            raise AssertionError("concurrent checkpoint publisher did not terminate")
        assert process.exitcode == 0
    outcomes = [queue.get(timeout=5) for _ in processes]
    successes = [value for status, value in outcomes if status == "success"]
    failures = [value for status, value in outcomes if status == "failure"]
    assert len(successes) == 1, f"concurrent publish successes: {outcomes}"
    assert len(failures) == 1, f"concurrent publish failures: {outcomes}"
    failure_text = failures[0].lower()
    assert any(
        token in failure_text for token in ("overwrite", "exist", "publication")
    ), failures[0]
    targets = list(concurrency_root.glob("bc-only-s1-*"))
    assert len(targets) == 1
    verify_recursive_checksums(targets[0])
    assert not list(concurrency_root.glob(".bc-only-s1.tmp-*"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="steel-module-finalize-check-") as raw:
        scratch = Path(raw).resolve()
        finalization = write_finalization(scratch)
        check_candidate_selection(scratch)

        validation, rows = load_managed_finalization(
            finalization, allow_test_mode=True, verify_root_files=False
        )
        assert validation["task_count"] == 32 and len(rows) == 32
        expect_failure(
            load_managed_finalization,
            finalization,
            verify_root_files=False,
            contains="refuses test-mode",
        )

        output_root = scratch / "checkpoints"
        checkpoint_dir = build_checkpoint(
            finalization,
            output_root,
            allow_test_mode=True,
            verify_root_files=False,
        )
        checkpoint = load_production_checkpoint(
            checkpoint_dir,
            allow_test_mode=True,
            verify_root_files=False,
        )
        assert checkpoint.manifest["state_id"] == "BC-ONLY-S1"
        assert checkpoint.manifest["task_count"] == 32
        assert checkpoint.manifest["event_count"] == 8000
        assert checkpoint.manifest["accepted_statistical_evidence"] is False
        assert checkpoint.manifest["test_mode"] is True
        assert len(checkpoint.task_rows) == 32
        assert not list(checkpoint_dir.rglob("*.root"))
        verify_recursive_checksums(checkpoint_dir)
        expect_failure(
            load_production_checkpoint,
            checkpoint_dir,
            verify_root_files=False,
            contains="refuses a test-mode",
        )
        expect_failure(
            build_checkpoint,
            finalization,
            output_root,
            allow_test_mode=True,
            verify_root_files=False,
            contains="refusing to overwrite",
        )
        check_recursive_tamper(checkpoint_dir, scratch)
        check_checkpoint_semantics(checkpoint_dir, rows, scratch)
        check_source_readiness_and_seed_identity(checkpoint_dir, scratch)

        unexpected_case = scratch / "unexpected-file-tamper"
        shutil.copytree(checkpoint_dir, unexpected_case)
        (unexpected_case / "unexpected.txt").write_text("tamper\n", encoding="utf-8")
        expect_failure(
            verify_recursive_checksums,
            unexpected_case,
            contains="file set mismatch",
        )
        check_publish_safety(scratch)
        assert sha256_file(checkpoint_dir / "SHA256SUMS")

    print(
        "steel-module managed finalization/checkpoint: PASS "
        "(32 tasks, 8000 events, tamper/concurrency/BC-ONLY-S1 gates)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
