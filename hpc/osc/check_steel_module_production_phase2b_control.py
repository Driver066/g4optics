#!/usr/bin/env python3
"""No-Slurm adversarial checks for the Phase-2B managed control plane."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from check_steel_module_managed_production import make_fixture
from manage_steel_module_production_attempt import (
    freeze_terminal_accounting,
    reconcile_attempt,
    submit_intent,
)
from steel_module_campaign_lib import sha256_file
from steel_module_production_phase2b_lib import (
    READINESS_CRITICAL_ARTIFACTS,
    append_attempt_event,
    attempt_state,
    load_attempt_intent,
    load_execution_companion,
    load_frozen_accounting,
    materialize_execution_companion,
    prepare_attempt_intent,
    read_attempt_events,
    require_pristine_readiness_workspace,
    selected_task_ids,
    verify_readiness_protected_artifacts,
)


class FakeSlurm:
    def __init__(self, execution: object, attempt_id: str, *, mode: str = "success") -> None:
        self.execution = execution
        self.attempt_id = attempt_id
        self.mode = mode
        self.calls: list[tuple[str, ...]] = []
        self.job_id = "9001"

    def _intent(self) -> dict[str, object]:
        return json.loads(
            (self.execution.directory / "intents" / self.attempt_id / "intent.json").read_text()
        )

    def _scontrol(self, *, state: str = "PENDING", reason: str = "JobHeldUser") -> str:
        intent = self._intent()
        scheduler = intent["scheduler"]
        wrapper = self.execution.directory / "intents" / self.attempt_id / "job-wrapper.sh"
        array = "31" if self.mode == "bad-array" else scheduler["array_spec"].split("-", 1)[1]
        return (
            f"JobId={self.job_id}_[1-{array}] ArrayJobId={self.job_id} "
            f"ArrayTaskId=1-{array} Account=PAS2524 JobName={scheduler['job_name']} "
            f"JobState={state} Reason={reason} Requeue=0 Command={wrapper} "
            f"WorkDir={self.execution.directory} StdOut={scheduler['output_pattern']}\n"
        )

    def __call__(self, command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(command))
        name = Path(command[0]).name
        if name == "sbatch":
            if self.mode in {"timeout", "accepted-timeout", "multi", "zero"}:
                raise subprocess.TimeoutExpired(command, timeout)
            if self.mode == "non-numeric":
                return subprocess.CompletedProcess(command, 0, "not-a-job\n", "")
            if self.mode == "non-zero":
                return subprocess.CompletedProcess(command, 17, "", "scheduler rejected")
            return subprocess.CompletedProcess(command, 0, self.job_id + "\n", "")
        if name == "scontrol" and command[1:3] == ["show", "job"]:
            if self.mode == "scontrol-missing-terminal":
                return subprocess.CompletedProcess(
                    command, 1, "", "job no longer visible to scontrol"
                )
            if self.mode == "released-reconcile":
                return subprocess.CompletedProcess(command, 0, self._scontrol(state="RUNNING", reason="None"), "")
            if self.mode == "not-user-held":
                return subprocess.CompletedProcess(
                    command, 0,
                    self._scontrol(reason="DependencyNeverSatisfied"), "",
                )
            return subprocess.CompletedProcess(command, 0, self._scontrol(), "")
        if name == "scontrol" and command[1] == "release":
            if self.mode == "release-timeout":
                self.mode = "released-reconcile"
                raise subprocess.TimeoutExpired(command, timeout)
            return subprocess.CompletedProcess(command, 0, "", "")
        if name == "squeue" and "--name" in command:
            intent = self._intent()
            token = intent["scheduler"]["job_name"]
            if self.mode == "multi":
                text = f"9001|{token}|1-32|PENDING|JobHeldUser\n9002|{token}|1-32|PENDING|JobHeldUser\n"
            elif self.mode in {"zero", "timeout"}:
                text = ""
            else:
                text = f"{self.job_id}|{token}|1-32|RUNNING|None\n"
            return subprocess.CompletedProcess(command, 0, text, "")
        if name == "sacct" and "--name" in command:
            if self.mode == "scontrol-missing-terminal":
                token = self._intent()["scheduler"]["job_name"]
                return subprocess.CompletedProcess(
                    command, 0,
                    f"{self.job_id}|{token}|COMPLETED|0:0\n", "",
                )
            return subprocess.CompletedProcess(command, 0, "", "")
        if name == "squeue":
            return subprocess.CompletedProcess(command, 0, "", "")
        if name == "sacct":
            state = "CANCELLED by 1234" if self.mode == "cancelled-accounting" else "COMPLETED"
            exit_code = "0:15" if self.mode == "cancelled-accounting" else "0:0"
            rows = f"{self.job_id}|{state}|{exit_code}|10||\n" + "".join(
                f"{self.job_id}_{index}|{state}|{exit_code}|10|100K|200K\n"
                for index in range(1, 33)
            )
            if self.mode == "duplicate-accounting":
                rows += f"{self.job_id}_1|COMPLETED|0:0|10|100K|200K\n"
            return subprocess.CompletedProcess(command, 0, rows, "")
        raise AssertionError(f"unexpected fake Slurm command: {command}")


def _pilot_analysis_fixture(pilot: Path) -> None:
    analysis = pilot / "finalized" / "analysis-v2"
    analysis.mkdir()
    config = {
        "schema_version": "steel-module-analysis-v2-config-v1",
        "accepted_statistical_evidence": False,
        "analysis_commit": "f" * 40,
    }
    (analysis / "analysis_config.json").write_text(json.dumps(config, indent=2) + "\n")
    (analysis / "fixture.csv").write_text("value\n1\n")
    (analysis / "SHA256SUMS").write_text(
        "".join(
            f"{sha256_file(analysis / name)}  {name}\n"
            for name in ("analysis_config.json", "fixture.csv")
        )
    )


def _fixture_sources(root: Path) -> tuple[Path, Path]:
    simulation = root / "simulation-source"
    control = root / "control-source"
    (simulation / "test/OpNovice2").mkdir(parents=True)
    (simulation / "test/OpNovice2/run_sipm_cavity_scan.sh").write_text("#!/bin/sh\nexit 0\n")
    (control / "hpc/osc").mkdir(parents=True)
    batch = control / "hpc/osc/submit_steel_module_production_phase2b.sbatch"
    batch.write_text("#!/bin/sh\nexit 0\n")
    batch.chmod(0o755)
    return simulation, control


def make_execution(repo_root: Path, scratch: Path, suffix: str) -> object:
    fixture = scratch / suffix
    fixture.mkdir()
    _, child = make_fixture(repo_root, fixture)
    pilot = fixture / "sealed-pilot"
    _pilot_analysis_fixture(pilot)
    sources = _fixture_sources(fixture)
    execution = materialize_execution_companion(
        repo_root=repo_root, managed_child_dir=child,
        out_dir=fixture / "execution", pilot_dir=pilot,
        test_mode=True, fixture_sources=sources,
    )
    assert execution.manifest["accepted_statistical_evidence"] is False
    assert execution.manifest["test_mode"] is True
    assert not (execution.directory / "campaign.json").exists()
    assert execution.manifest["artifacts"]["control_lock"]["inode"] == (
        execution.directory / ".control.lock"
    ).stat().st_ino
    assert not (execution.directory / "sources" / "control" / ".git").exists()
    return execution


def test_materialization(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "materialize")
    require_pristine_readiness_workspace(execution)
    short_actor_intent = prepare_attempt_intent(
        execution, attempt_id="20260718T115900Z-check", mode="initial",
        actor="edison", write=False, readiness_lock_sha256="a" * 64,
    )
    assert short_actor_intent["created_by"] == "edison"
    orphan = execution.directory / "attempts" / "orphan-without-intent"
    orphan.mkdir()
    try:
        require_pristine_readiness_workspace(execution)
    except ValueError as exc:
        assert "empty attempts" in str(exc)
    else:
        raise AssertionError("orphan attempt evidence was accepted for readiness")
    orphan.rmdir()
    finalized_orphan = execution.directory / "finalized" / "orphan"
    finalized_orphan.mkdir()
    try:
        require_pristine_readiness_workspace(execution)
    except ValueError as exc:
        assert "empty finalized" in str(exc)
    else:
        raise AssertionError("orphan finalized evidence was accepted for readiness")
    finalized_orphan.rmdir()
    before = sha256_file(execution.directory / "managed_execution.json")
    try:
        materialize_execution_companion(
            repo_root=repo_root, managed_child_dir=execution.managed_child.directory,
            out_dir=execution.directory, pilot_dir=Path(execution.manifest["sealed_pilot"]["directory"]),
            test_mode=True,
        )
    except ValueError as exc:
        assert "overwrite" in str(exc)
    else:
        raise AssertionError("execution companion overwrite was accepted")
    assert sha256_file(execution.directory / "managed_execution.json") == before

    manifest = execution.directory / "managed_execution.json"
    original = manifest.read_bytes()
    manifest.write_bytes(original + b" ")
    try:
        load_execution_companion(execution.directory, allow_test_mode=True, require_readiness=False)
    except ValueError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("tampered execution manifest was accepted")
    manifest.write_bytes(original)

    fault = scratch / "fault"
    fixture = scratch / "fault-input"
    fixture.mkdir()
    _, child = make_fixture(repo_root, fixture)
    pilot = fixture / "sealed-pilot"
    _pilot_analysis_fixture(pilot)
    try:
        materialize_execution_companion(
            repo_root=repo_root, managed_child_dir=child, out_dir=fault,
            pilot_dir=pilot, test_mode=True, fixture_sources=_fixture_sources(fixture),
            fault_after="sources",
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("injected execution fault succeeded")
    assert not fault.exists()


def test_readiness_protected_artifact_set(scratch: Path) -> None:
    root = scratch / "readiness-current"
    execution_root = scratch / "readiness-execution"
    frozen = execution_root / "sources" / "control"
    root.mkdir()
    frozen.mkdir(parents=True)
    artifacts: dict[str, dict[str, str]] = {}
    for relative in READINESS_CRITICAL_ARTIFACTS:
        current_path = root / relative
        frozen_path = frozen / relative
        current_path.parent.mkdir(parents=True, exist_ok=True)
        frozen_path.parent.mkdir(parents=True, exist_ok=True)
        content = f"protected:{relative}\n"
        current_path.write_text(content, encoding="utf-8")
        frozen_path.write_text(content, encoding="utf-8")
        artifacts[relative] = {"sha256": sha256_file(current_path)}
    execution = SimpleNamespace(
        directory=execution_root,
        manifest={"sources": {"control_plane": {"path": "sources/control"}}},
    )
    verify_readiness_protected_artifacts(
        execution, root, {"artifacts": artifacts}
    )

    incomplete = dict(artifacts)
    incomplete.pop(READINESS_CRITICAL_ARTIFACTS[-1])
    try:
        verify_readiness_protected_artifacts(
            execution, root, {"artifacts": incomplete}
        )
    except ValueError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("partial readiness protected set was accepted")

    victim = READINESS_CRITICAL_ARTIFACTS[0]
    (root / victim).write_text("tampered-current\n", encoding="utf-8")
    try:
        verify_readiness_protected_artifacts(
            execution, root, {"artifacts": artifacts}
        )
    except ValueError as exc:
        assert "changed" in str(exc)
    else:
        raise AssertionError("tampered R-checkout artifact was accepted")
    (root / victim).write_text(f"protected:{victim}\n", encoding="utf-8")
    (frozen / victim).write_text("tampered-frozen\n", encoding="utf-8")
    try:
        verify_readiness_protected_artifacts(
            execution, root, {"artifacts": artifacts}
        )
    except ValueError as exc:
        assert "commit C" in str(exc)
    else:
        raise AssertionError("tampered implementation-C artifact was accepted")


def _rewrite_intent(directory: Path, value: dict[str, object]) -> None:
    unhashed = dict(value)
    unhashed.pop("intent_sha256", None)
    value["intent_sha256"] = hashlib.sha256(
        json.dumps(
            unhashed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    intent_path = directory / "intent.json"
    checksum_path = directory / "SHA256SUMS"
    intent_path.chmod(0o644)
    checksum_path.chmod(0o644)
    intent_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    checksum_path.write_text(
        "".join(
            f"{sha256_file(directory / name)}  {name}\n"
            for name in ("intent.json", "job-wrapper.sh", "scan_args.txt", "tasks.tsv")
        ),
        encoding="utf-8",
    )


def test_intent_contract_tamper(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "intent-contract")
    intent = _prepare(execution, "20260718T120700Z-initial")
    directory = execution.directory / "intents" / intent["attempt_id"]

    scheduler_tamper = json.loads(json.dumps(intent))
    scheduler_tamper["scheduler"]["job_wrapper"]["path"] = "../../arbitrary.sh"
    _rewrite_intent(directory, scheduler_tamper)
    try:
        load_attempt_intent(execution, intent["attempt_id"])
    except ValueError as exc:
        assert "scheduler contract" in str(exc)
    else:
        raise AssertionError("self-rehashed scheduler override was accepted")

    runtime_tamper = json.loads(json.dumps(intent))
    runtime_tamper["runtime"]["executable_sha256"] = "0" * 64
    _rewrite_intent(directory, runtime_tamper)
    try:
        load_attempt_intent(execution, intent["attempt_id"])
    except ValueError as exc:
        assert "runtime" in str(exc)
    else:
        raise AssertionError("self-rehashed runtime override was accepted")

    wrapper = directory / "job-wrapper.sh"
    wrapper.chmod(0o644)
    wrapper.write_text("#!/usr/bin/env bash\nexec /tmp/arbitrary\n", encoding="utf-8")
    wrapper_tamper = json.loads(json.dumps(intent))
    wrapper_sha = sha256_file(wrapper)
    wrapper_tamper["artifacts"]["job_wrapper"]["sha256"] = wrapper_sha
    wrapper_tamper["scheduler"]["job_wrapper"]["sha256"] = wrapper_sha
    _rewrite_intent(directory, wrapper_tamper)
    try:
        load_attempt_intent(execution, intent["attempt_id"])
    except ValueError as exc:
        assert "wrapper content" in str(exc)
    else:
        raise AssertionError("self-rehashed arbitrary wrapper was accepted")


def _prepare(execution: object, attempt_id: str) -> dict[str, object]:
    checked = prepare_attempt_intent(
        execution, attempt_id=attempt_id, mode="initial", actor="fixture-reviewer",
        write=False, readiness_lock_sha256="a" * 64,
    )
    assert not (execution.directory / "intents" / attempt_id).exists()
    written = prepare_attempt_intent(
        execution, attempt_id=attempt_id, mode="initial", actor="fixture-reviewer",
        write=True, readiness_lock_sha256="a" * 64,
    )
    assert written["intent_sha256"] == checked["intent_sha256"] or (
        # created_at is evidence, so separate check/write calls need not hash equal.
        written["selected"] == checked["selected"]
    )
    assert written["selected"]["task_count"] == 32
    assert (execution.directory / "intents" / attempt_id / "job-wrapper.sh").is_file()
    assert "json.load" not in (
        execution.directory / "intents" / attempt_id / "job-wrapper.sh"
    ).read_text(encoding="utf-8")
    return written


def test_success(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "success")
    intent = _prepare(execution, "20260718T120000Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"])
    job_id = submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer", runner=fake,
    )
    assert job_id == "9001"
    assert attempt_state(execution, intent["attempt_id"]).status == "released-active"
    assert sum(1 for command in fake.calls if Path(command[0]).name == "sbatch") == 1
    sbatch_command = next(
        command for command in fake.calls if Path(command[0]).name == "sbatch"
    )
    assert sbatch_command[-3:] == (
        str(execution.directory), intent["attempt_id"], intent["intent_sha256"]
    )
    accounting = freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer", runner=fake,
    )
    assert accounting["accepted_terminal"] is True
    assert accounting["all_tasks_completed"] is True
    assert attempt_state(execution, intent["attempt_id"]).status == "terminal-accounting-frozen"

    first_task = execution.tasks[0]
    marker_dir = (
        execution.directory / "attempts" / intent["attempt_id"]
        / "tasks" / first_task.logical_task_id
    )
    marker_dir.mkdir(parents=True)
    artifact_records = {}
    for artifact_name in (
        "run_config", "points", "macro", "simulation_log", "root", "summary",
        "efficiency_map",
    ):
        artifact_path = marker_dir / f"fixture-{artifact_name}"
        artifact_path.write_text(f"{artifact_name}\n")
        artifact_records[artifact_name] = {
            "path": artifact_path.relative_to(execution.directory).as_posix(),
            "size_bytes": artifact_path.stat().st_size,
            "sha256": sha256_file(artifact_path),
        }
    (marker_dir / "task_result.json").write_text(
        json.dumps(
            {
                "schema_version": "steel-module-managed-production-task-result-v1",
                "execution_id": execution.execution_id,
                "execution_hash": execution.execution_hash,
                "intent_sha256": intent["intent_sha256"],
                "attempt_id": intent["attempt_id"],
                "logical_task_id": first_task.logical_task_id,
                "configuration_hash": first_task.configuration_hash,
                "seed1": first_task.seed1, "seed2": first_task.seed2,
                "events": first_task.events,
                "campaign_id": execution.campaign_id,
                "plan_hash": execution.plan_hash,
                "program_id": execution.manifest["program"]["program_id"],
                "program_hash": execution.manifest["program"]["program_hash"],
                "child_id": "BC-S1",
                "child_plan_hash": execution.manifest["managed_child"]["child_plan_hash"],
                "binding_hash": execution.manifest["managed_child"]["binding_hash"],
                "phase2a_lock_sha256": execution.manifest["phase2a_lock"]["sha256"],
                "readiness_lock_sha256": intent["readiness_lock_sha256"],
                "task_index": first_task.task_index,
                "tile_thickness_mm": first_task.tile_thickness_mm,
                "sipm_layout": first_task.sipm_layout,
                "absorber_transverse_mm": first_task.absorber_transverse_mm,
                "seed_block": first_task.seed_block,
                "simulation_source": execution.manifest["sources"]["simulation"],
                "control_plane_source": execution.manifest["sources"]["control_plane"],
                "runtime": execution.manifest["runtime"],
                "artifacts": artifact_records,
            }, indent=2,
        ) + "\n"
    )
    assert len(selected_task_ids(execution, "resume")) == 31
    assert len(selected_task_ids(execution, "retry-failed")) == 31
    assert first_task.logical_task_id not in selected_task_ids(execution, "resume")

    event = execution.directory / "attempts" / intent["attempt_id"] / "events"
    first = sorted(event.iterdir())[0]
    first.chmod(0o644)
    original = first.read_bytes()
    first.write_bytes(original.replace(b"submission-invoked", b"submission-invokex"))
    try:
        read_attempt_events(execution, intent["attempt_id"])
    except ValueError:
        pass
    else:
        raise AssertionError("tampered event chain was accepted")


def test_accounting_publish_crash_recovery(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "accounting-recovery")
    intent = _prepare(execution, "20260718T120500Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"])
    submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    event_path = next(
        (execution.directory / "attempts" / intent["attempt_id"] / "events").glob(
            "*-terminal-accounting-frozen-*.json"
        )
    )
    event_path.unlink()
    assert attempt_state(execution, intent["attempt_id"]).status == "released-active"

    def forbidden_runner(*args: object, **kwargs: object) -> object:
        raise AssertionError("accounting recovery unexpectedly queried Slurm")

    recovered = freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=forbidden_runner,
    )
    assert recovered["accepted_terminal"] is True
    assert attempt_state(execution, intent["attempt_id"]).status == "terminal-accounting-frozen"
    last = read_attempt_events(execution, intent["attempt_id"])[-1]
    assert last["payload"]["recovered_after_publish"] is True


def test_duplicate_accounting_rejected(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "duplicate-accounting")
    intent = _prepare(execution, "20260718T120600Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="duplicate-accounting")
    submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    try:
        freeze_terminal_accounting(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
    except ValueError as exc:
        assert "exact array-task" in str(exc)
    else:
        raise AssertionError("duplicate sacct array-task row was accepted")
    assert not (
        execution.directory / "attempts" / intent["attempt_id"] / "accounting"
    ).exists()


def test_cancelled_accounting_enables_retry(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "cancelled-accounting")
    intent = _prepare(execution, "20260718T120700Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="cancelled-accounting")
    submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    accounting = freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    assert accounting["all_tasks_completed"] is False
    assert accounting["task_states"]["1"] == "CANCELLED by 1234"
    assert attempt_state(execution, intent["attempt_id"]).status == "terminal-accounting-frozen"
    assert len(selected_task_ids(execution, "retry-failed")) == 32


def test_single_nonterminal_intent_gate(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "single-nonterminal-intent")
    first = _prepare(execution, "20260718T120800Z-initial")
    try:
        prepare_attempt_intent(
            execution, attempt_id="20260718T120900Z-resume", mode="resume",
            actor="fixture-reviewer", write=False,
            readiness_lock_sha256="b" * 64,
        )
    except ValueError as exc:
        assert "prior intent" in str(exc)
    else:
        raise AssertionError("a second intent was prepared while the first was non-terminal")

    append_attempt_event(
        execution, first["attempt_id"], "intent-cancelled",
        {"reason": "fixture creates a second intent before restoring the first"},
        actor="fixture-reviewer",
    )
    second = prepare_attempt_intent(
        execution, attempt_id="20260718T120900Z-resume", mode="resume",
        actor="fixture-reviewer", write=True, readiness_lock_sha256="b" * 64,
    )
    cancellation = next(
        (execution.directory / "attempts" / first["attempt_id"] / "events").glob(
            "*-intent-cancelled-*.json"
        )
    )
    cancellation.unlink()
    fake = FakeSlurm(execution, second["attempt_id"])
    try:
        submit_intent(
            execution, attempt_id=first["attempt_id"],
            intent_sha256=first["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("submission ignored a competing prepared intent")
    assert not any(Path(command[0]).name == "sbatch" for command in fake.calls)


def test_accounting_event_binding(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "accounting-event-binding")
    intent = _prepare(execution, "20260718T120950Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"])
    submit_intent(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    freeze_terminal_accounting(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    )
    accounting = (
        execution.directory / "attempts" / intent["attempt_id"] / "accounting"
    )
    frozen = accounting / "frozen.json"
    value = json.loads(frozen.read_text())
    value["all_tasks_completed"] = False
    frozen.chmod(0o644)
    frozen.write_text(json.dumps(value, indent=2) + "\n")
    files = ("frozen.json", "sacct.psv", "squeue.psv")
    (accounting / "SHA256SUMS").chmod(0o644)
    (accounting / "SHA256SUMS").write_text(
        "".join(f"{sha256_file(accounting / name)}  {name}\n" for name in files)
    )
    try:
        load_frozen_accounting(execution, intent["attempt_id"])
    except ValueError as exc:
        assert "event binding" in str(exc)
    else:
        raise AssertionError("self-rehashed accounting escaped its terminal-event binding")


def test_ambiguity(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "ambiguity")
    intent = _prepare(execution, "20260718T121000Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="timeout")
    try:
        submit_intent(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer", runner=fake,
        )
    except ValueError as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("submission timeout was accepted")
    assert attempt_state(execution, intent["attempt_id"]).unresolved
    fake.mode = "zero"
    assert reconcile_attempt(
        execution, attempt_id=intent["attempt_id"], intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer", runner=fake, now_utc="2026-07-18T12:00:00+00:00",
    ) == "zero-match-observation-recorded"
    assert reconcile_attempt(
        execution, attempt_id=intent["attempt_id"], intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer", runner=fake, now_utc="2026-07-18T12:11:00+00:00",
        confirm_no_job=True, rationale="Two independent OSC observations found no job.",
    ) == "no-job-confirmed"
    assert attempt_state(execution, intent["attempt_id"]).status == "cancelled"
    assert sum(1 for command in fake.calls if Path(command[0]).name == "sbatch") == 1


def test_submission_failure_shapes(repo_root: Path, scratch: Path) -> None:
    for suffix, mode in (("nonnumeric", "non-numeric"), ("nonzero", "non-zero")):
        execution = make_execution(repo_root, scratch, suffix)
        intent = _prepare(execution, f"20260718T1215{len(suffix):02d}Z-initial")
        fake = FakeSlurm(execution, intent["attempt_id"], mode=mode)
        try:
            submit_intent(
                execution, attempt_id=intent["attempt_id"],
                intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
                runner=fake,
            )
        except ValueError as exc:
            assert "ambiguous" in str(exc)
        else:
            raise AssertionError(f"{mode} sbatch result was accepted")
        assert attempt_state(execution, intent["attempt_id"]).unresolved
        assert sum(1 for command in fake.calls if Path(command[0]).name == "sbatch") == 1


def test_submission_adoption_and_multiple_match(repo_root: Path, scratch: Path) -> None:
    adopted = make_execution(repo_root, scratch, "adopted")
    intent = _prepare(adopted, "20260718T121700Z-initial")
    fake = FakeSlurm(adopted, intent["attempt_id"], mode="accepted-timeout")
    try:
        submit_intent(
            adopted, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("accepted-but-lost submission receipt was not quarantined")
    assert reconcile_attempt(
        adopted, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    ) == "reconciled-same-job:9001:PENDING"
    assert attempt_state(adopted, intent["attempt_id"]).status == "released-active"
    assert sum(1 for command in fake.calls if Path(command[0]).name == "sbatch") == 1

    multiple = make_execution(repo_root, scratch, "multiple")
    multi_intent = _prepare(multiple, "20260718T121800Z-initial")
    multi_fake = FakeSlurm(multiple, multi_intent["attempt_id"], mode="multi")
    try:
        submit_intent(
            multiple, attempt_id=multi_intent["attempt_id"],
            intent_sha256=multi_intent["intent_sha256"], actor="fixture-reviewer",
            runner=multi_fake,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("multiple-match fixture did not enter quarantine")
    try:
        reconcile_attempt(
            multiple, attempt_id=multi_intent["attempt_id"],
            intent_sha256=multi_intent["intent_sha256"], actor="fixture-reviewer",
            runner=multi_fake,
        )
    except ValueError as exc:
        assert "multiple" in str(exc)
    else:
        raise AssertionError("multiple scheduler matches were adopted")
    assert attempt_state(multiple, multi_intent["attempt_id"]).unresolved
    assert sum(1 for command in multi_fake.calls if Path(command[0]).name == "sbatch") == 1


def test_release_reconcile(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "release")
    intent = _prepare(execution, "20260718T122000Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="release-timeout")
    try:
        submit_intent(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer", runner=fake,
        )
    except ValueError as exc:
        assert "release" in str(exc)
    else:
        raise AssertionError("release timeout was accepted")
    before = sum(1 for command in fake.calls if Path(command[0]).name == "sbatch")
    result = reconcile_attempt(
        execution, attempt_id=intent["attempt_id"], intent_sha256=intent["intent_sha256"],
        actor="fixture-reviewer", runner=fake,
    )
    assert result == "reconciled-same-job:9001:RUNNING"
    assert attempt_state(execution, intent["attempt_id"]).status == "released-active"
    assert sum(1 for command in fake.calls if Path(command[0]).name == "sbatch") == before


def test_release_zero_match_stays_quarantined(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "release-zero")
    intent = _prepare(execution, "20260718T122500Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="release-timeout")
    try:
        submit_intent(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("release timeout was not quarantined")
    fake.mode = "zero"
    assert reconcile_attempt(
        execution, attempt_id=intent["attempt_id"],
        intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
        runner=fake,
    ) == "release-zero-match-observation-recorded"
    try:
        reconcile_attempt(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake, confirm_no_job=True, rationale="not sufficient",
        )
    except ValueError as exc:
        assert "known submitted job" in str(exc)
    else:
        raise AssertionError("known released job was cleared as never submitted")
    assert attempt_state(execution, intent["attempt_id"]).unresolved


def _seed_known_held_state(
    execution: object, intent: dict[str, object], *, verified: bool
) -> None:
    append_attempt_event(
        execution, intent["attempt_id"], "submission-invoked",
        {"command_sha256": "a" * 64, "job_name": intent["scheduler"]["job_name"]},
        actor="fixture-reviewer",
    )
    append_attempt_event(
        execution, intent["attempt_id"], "submitted-held",
        {"job_id": "9001"}, actor="fixture-reviewer",
    )
    if verified:
        append_attempt_event(
            execution, intent["attempt_id"], "job-verified",
            {"job_id": "9001", "job_name": intent["scheduler"]["job_name"],
             "state": "PENDING"}, actor="fixture-reviewer",
        )


def test_known_held_crash_recovery(repo_root: Path, scratch: Path) -> None:
    for suffix, verified in (("submitted-crash", False), ("verified-crash", True)):
        execution = make_execution(repo_root, scratch, suffix)
        intent = _prepare(
            execution,
            "20260718T124000Z-initial" if not verified
            else "20260718T124100Z-initial",
        )
        _seed_known_held_state(execution, intent, verified=verified)
        fake = FakeSlurm(execution, intent["attempt_id"])
        result = reconcile_attempt(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer",
            runner=fake,
        )
        assert result.startswith("reconciled-same-job:9001:")
        assert attempt_state(execution, intent["attempt_id"]).status == "released-active"
        assert not any(Path(command[0]).name == "sbatch" for command in fake.calls)
        assert sum(
            1 for command in fake.calls
            if Path(command[0]).name == "scontrol" and command[1] == "release"
        ) == 1

    released = make_execution(repo_root, scratch, "verified-after-release-crash")
    released_intent = _prepare(released, "20260718T124200Z-initial")
    _seed_known_held_state(released, released_intent, verified=True)
    released_fake = FakeSlurm(
        released, released_intent["attempt_id"], mode="released-reconcile"
    )
    result = reconcile_attempt(
        released, attempt_id=released_intent["attempt_id"],
        intent_sha256=released_intent["intent_sha256"],
        actor="fixture-reviewer", runner=released_fake,
    )
    assert result == "reconciled-same-job:9001:RUNNING"
    assert attempt_state(released, released_intent["attempt_id"]).status == "released-active"
    assert not any(Path(command[0]).name == "sbatch" for command in released_fake.calls)
    assert not any(
        Path(command[0]).name == "scontrol" and command[1] == "release"
        for command in released_fake.calls
    )

    terminal = make_execution(repo_root, scratch, "verified-terminal-sacct")
    terminal_intent = _prepare(terminal, "20260718T124300Z-initial")
    _seed_known_held_state(terminal, terminal_intent, verified=True)
    terminal_fake = FakeSlurm(
        terminal, terminal_intent["attempt_id"],
        mode="scontrol-missing-terminal",
    )
    result = reconcile_attempt(
        terminal, attempt_id=terminal_intent["attempt_id"],
        intent_sha256=terminal_intent["intent_sha256"],
        actor="fixture-reviewer", runner=terminal_fake,
    )
    assert result == "reconciled-terminal-sacct:9001:COMPLETED"
    assert attempt_state(terminal, terminal_intent["attempt_id"]).status == "released-active"
    assert not any(Path(command[0]).name == "sbatch" for command in terminal_fake.calls)
    frozen = freeze_terminal_accounting(
        terminal, attempt_id=terminal_intent["attempt_id"],
        intent_sha256=terminal_intent["intent_sha256"],
        actor="fixture-reviewer", runner=terminal_fake,
    )
    assert frozen["all_tasks_completed"] is True


def test_event_concurrency_and_path_escape(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "event-concurrency")
    intent = _prepare(execution, "20260718T123500Z-initial")
    staging = (
        execution.directory / "attempts" / intent["attempt_id"] / "event-staging"
    )
    staging.mkdir(parents=True)
    (staging / ".unpublished-event-crash-residual").write_text(
        "partial", encoding="utf-8"
    )

    def cancel() -> str:
        try:
            append_attempt_event(
                execution, intent["attempt_id"], "intent-cancelled",
                {"reason": "concurrent-fixture"}, actor="fixture-reviewer",
            )
        except ValueError:
            return "rejected"
        return "written"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _: cancel(), range(2)))
    assert outcomes == ["rejected", "written"]
    assert len(read_attempt_events(execution, intent["attempt_id"])) == 1
    assert attempt_state(execution, intent["attempt_id"]).status == "cancelled"
    assert not staging.exists()

    escaped = make_execution(repo_root, scratch, "event-symlink")
    escaped_intent = _prepare(escaped, "20260718T123600Z-initial")
    outside = scratch / "outside-events"
    outside.mkdir()
    attempt_link = escaped.directory / "attempts" / escaped_intent["attempt_id"]
    attempt_link.symlink_to(outside, target_is_directory=True)
    try:
        append_attempt_event(
            escaped, escaped_intent["attempt_id"], "intent-cancelled",
            {"reason": "symlink-fixture"}, actor="fixture-reviewer",
        )
    except ValueError as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("symlinked attempt directory was accepted")
    assert not list(outside.iterdir())


def test_array_tamper(repo_root: Path, scratch: Path) -> None:
    execution = make_execution(repo_root, scratch, "array")
    intent = _prepare(execution, "20260718T123000Z-initial")
    fake = FakeSlurm(execution, intent["attempt_id"], mode="bad-array")
    try:
        submit_intent(
            execution, attempt_id=intent["attempt_id"],
            intent_sha256=intent["intent_sha256"], actor="fixture-reviewer", runner=fake,
        )
    except ValueError as exc:
        assert "quarantined" in str(exc)
    else:
        raise AssertionError("wrong Slurm array shape was accepted")
    assert attempt_state(execution, intent["attempt_id"]).status == "release-ambiguous"

    held = make_execution(repo_root, scratch, "held-reason")
    held_intent = _prepare(held, "20260718T123100Z-initial")
    held_fake = FakeSlurm(held, held_intent["attempt_id"], mode="not-user-held")
    try:
        submit_intent(
            held, attempt_id=held_intent["attempt_id"],
            intent_sha256=held_intent["intent_sha256"],
            actor="fixture-reviewer", runner=held_fake,
        )
    except ValueError as exc:
        assert "quarantined" in str(exc)
    else:
        raise AssertionError("non-user-held Slurm job was released")
    assert attempt_state(held, held_intent["attempt_id"]).status == "release-ambiguous"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    help_result = subprocess.run(
        [sys.executable, "hpc/osc/manage_steel_module_production_attempt.py", "--help"],
        cwd=repo_root, text=True, stdout=subprocess.PIPE, check=True,
    )
    assert "sbatch-command" not in help_result.stdout
    assert "execution-dir" not in help_result.stdout
    with tempfile.TemporaryDirectory(prefix="steel-module-phase2b-control-") as temporary:
        scratch = Path(temporary)
        sentinel = scratch / "real-sbatch-was-invoked"
        sentinel_bin = scratch / "sentinel-bin"
        sentinel_bin.mkdir()
        sbatch = sentinel_bin / "sbatch"
        sbatch.write_text(
            "#!/usr/bin/env bash\nprintf invoked > " + str(sentinel) + "\nexit 99\n",
            encoding="utf-8",
        )
        sbatch.chmod(0o755)
        original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(sentinel_bin) + os.pathsep + original_path
        test_materialization(repo_root, scratch)
        test_readiness_protected_artifact_set(scratch)
        test_intent_contract_tamper(repo_root, scratch)
        test_success(repo_root, scratch)
        test_accounting_publish_crash_recovery(repo_root, scratch)
        test_duplicate_accounting_rejected(repo_root, scratch)
        test_cancelled_accounting_enables_retry(repo_root, scratch)
        test_single_nonterminal_intent_gate(repo_root, scratch)
        test_accounting_event_binding(repo_root, scratch)
        test_ambiguity(repo_root, scratch)
        test_submission_failure_shapes(repo_root, scratch)
        test_submission_adoption_and_multiple_match(repo_root, scratch)
        test_release_reconcile(repo_root, scratch)
        test_release_zero_match_stays_quarantined(repo_root, scratch)
        test_known_held_crash_recovery(repo_root, scratch)
        test_array_tamper(repo_root, scratch)
        test_event_concurrency_and_path_escape(repo_root, scratch)
        assert not sentinel.exists(), "a readiness test invoked the real sbatch command"
        os.environ["PATH"] = original_path
    print("steel-module Phase-2B control plane: PASS")
    print("scheduler: in-process fake only; real Slurm calls: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
