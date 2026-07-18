#!/usr/bin/env python3
"""Manage checksum-bound BC-S1 intents and fail-closed Slurm attempts.

The formal interface has no scheduler, account, project, child, or path
override.  Tests exercise the internal functions with an in-process fake
runner; the command-line interface can invoke only the OSC commands named in
this file.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from steel_module_campaign_lib import load_json, require_dict, sha256_file
from steel_module_production_phase2b_lib import (
    ACCOUNTING_SCHEMA_VERSION,
    FORMAL_ACCOUNT,
    READINESS_LOCK_RELATIVE,
    TERMINAL_FAILURE_STATES,
    ManagedExecution,
    append_attempt_event,
    attempt_state,
    base_slurm_state,
    execution_lock,
    list_attempt_ids,
    load_attempt_intent,
    load_execution_companion,
    load_frozen_accounting,
    load_phase2a_lock,
    prepare_attempt_intent,
    fsync_tree,
    fsync_directory,
    utc_now,
)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
JOB_ID_RE = re.compile(r"^[1-9][0-9]*$")


def _run(
    command: Sequence[str], *, cwd: Path, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False, timeout=timeout,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "")},
    )


def _run_with(
    runner: CommandRunner, command: Sequence[str], *, cwd: Path, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    return runner(list(command), cwd=cwd, timeout=timeout)


def _formal_execution(repo_root: Path, *, readiness: bool) -> ManagedExecution:
    _, phase2a = load_phase2a_lock(repo_root)
    child = Path(phase2a["canonical_directory"])
    execution_dir = child.parent / "steel-module-production-bc-s1-execution"
    return load_execution_companion(
        execution_dir, repo_root=repo_root, require_readiness=readiness
    )


def _readiness_sha(repo_root: Path) -> str:
    path = repo_root / READINESS_LOCK_RELATIVE
    if not path.is_file():
        raise ValueError("tracked Phase-2B readiness lock is missing")
    return sha256_file(path)


def _verify_intent_hash(intent: dict[str, Any], expected: str) -> None:
    if intent.get("intent_sha256") != expected:
        raise ValueError("explicit intent SHA-256 does not match the frozen intent")


def submission_command(execution: ManagedExecution, intent: dict[str, Any]) -> list[str]:
    scheduler = intent["scheduler"]
    attempt_id = intent["attempt_id"]
    wrapper = execution.directory / "intents" / attempt_id / scheduler["job_wrapper"]["path"]
    return [
        "sbatch", "--hold", "--parsable", "--no-requeue", "--export=NONE",
        "--time=01:00:00", "--nodes=1", "--ntasks=1", "--cpus-per-task=1",
        "--mem=2G",
        "--account", FORMAL_ACCOUNT, "--job-name", scheduler["job_name"],
        "--array", scheduler["array_spec"], "--output", scheduler["output_pattern"],
        str(wrapper), str(execution.directory), attempt_id, intent["intent_sha256"],
    ]


def _parse_scontrol(output: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        row: dict[str, str] = {}
        for token in shlex.split(raw):
            if "=" in token:
                key, value = token.split("=", 1)
                row[key] = value
        if row:
            rows.append(row)
    return rows


def _validate_held_job(
    execution: ManagedExecution,
    intent: dict[str, Any],
    job_id: str,
    *,
    runner: CommandRunner,
    require_held: bool = True,
) -> dict[str, str]:
    result = _run_with(
        runner, ["scontrol", "show", "job", job_id, "-o"], cwd=execution.directory
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "scontrol show job failed")
    rows = _parse_scontrol(result.stdout)
    array_rows = [
        row for row in rows
        if row.get("ArrayJobId") == job_id
        or row.get("JobId") == job_id
        or row.get("JobId", "").startswith(job_id + "_")
    ]
    if not array_rows:
        raise ValueError("scontrol did not return the exact array job")
    scheduler = intent["scheduler"]
    expected = {
        "Account": FORMAL_ACCOUNT,
        "JobName": scheduler["job_name"],
        "Command": str(
            execution.directory / "intents" / intent["attempt_id"]
            / scheduler["job_wrapper"]["path"]
        ),
        "WorkDir": str(execution.directory),
        "Requeue": "0",
    }
    output_pattern = scheduler["output_pattern"]
    output_regex = re.compile(
        "^" + re.escape(output_pattern)
        .replace(re.escape("%A"), re.escape(job_id))
        .replace(re.escape("%a"), r"(?:[0-9]+|\[[0-9,%-]+\])") + "$"
    )
    observed_indices: set[int] = set()
    for row in array_rows:
        for key, value in expected.items():
            if row.get(key) != value:
                raise ValueError(f"Slurm held job {key} mismatch")
        stdout = row.get("StdOut", "")
        if stdout != output_pattern and not output_regex.fullmatch(stdout):
            raise ValueError("Slurm held job output pattern mismatch")
        if require_held and (
            row.get("JobState") != "PENDING"
            or row.get("Reason") != "JobHeldUser"
        ):
            raise ValueError("Slurm job is not held by the submitting user")
        expression = row.get("ArrayTaskId", "")
        if expression in {"", "N/A"}:
            job_value = row.get("JobId", "")
            if "_" in job_value:
                expression = job_value.split("_", 1)[1].strip("[]")
        expression = expression.split("%", 1)[0].strip("[]")
        for part in expression.split(","):
            if not part:
                continue
            if "-" in part:
                start, end = part.split("-", 1)
                if not start.isdigit() or not end.isdigit():
                    raise ValueError("invalid Slurm array-task expression")
                observed_indices.update(range(int(start), int(end) + 1))
            elif part.isdigit():
                observed_indices.add(int(part))
            else:
                raise ValueError("invalid Slurm array-task expression")
    expected_indices = set(range(1, intent["selected"]["task_count"] + 1))
    if observed_indices != expected_indices:
        raise ValueError("Slurm held job array shape mismatch")
    return array_rows[0]


def submit_intent(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    runner: CommandRunner = _run,
) -> str:
    """Contact Slurm once, initially held; any uncertainty is quarantined."""

    intent = load_attempt_intent(execution, attempt_id)
    _verify_intent_hash(intent, intent_sha256)
    if attempt_state(execution, attempt_id).status != "prepared":
        raise ValueError("only a prepared intent may be submitted")
    if intent.get("readiness_lock_sha256") is None:
        raise ValueError("intent is not bound to a readiness lock")
    command = submission_command(execution, intent)
    with execution_lock(execution.directory):
        current_intent = load_attempt_intent(execution, attempt_id)
        _verify_intent_hash(current_intent, intent_sha256)
        attempt_ids = list_attempt_ids(execution)
        states = {value: attempt_state(execution, value) for value in attempt_ids}
        if states[attempt_id].status != "prepared":
            raise ValueError("attempt state changed concurrently")
        competing = [
            value for value, state in states.items()
            if value != attempt_id
            and state.status not in {"cancelled", "terminal-accounting-frozen"}
        ]
        if competing:
            raise ValueError(
                "another intent is prepared, active, or unresolved; submission is locked"
            )
        append_attempt_event(
            execution, attempt_id, "submission-invoked",
            {"command_sha256": __import__("hashlib").sha256("\0".join(command).encode()).hexdigest(),
             "job_name": intent["scheduler"]["job_name"]}, actor=actor,
            lock_held=True,
        )
    try:
        result = _run_with(runner, command, cwd=execution.directory)
    except (OSError, subprocess.TimeoutExpired) as exc:
        append_attempt_event(
            execution, attempt_id, "submission-ambiguous",
            {"reason": type(exc).__name__}, actor=actor,
        )
        raise ValueError("Slurm submission outcome is ambiguous; do not retry") from exc
    raw = result.stdout.strip().split(";", 1)[0]
    if result.returncode != 0 or not JOB_ID_RE.fullmatch(raw):
        append_attempt_event(
            execution, attempt_id, "submission-ambiguous",
            {"return_code": result.returncode, "stdout": result.stdout[-1000:],
             "stderr": result.stderr[-1000:]}, actor=actor,
        )
        raise ValueError("Slurm submission outcome is ambiguous; do not retry")
    job_id = raw
    append_attempt_event(
        execution, attempt_id, "submitted-held", {"job_id": job_id}, actor=actor
    )
    try:
        row = _validate_held_job(execution, intent, job_id, runner=runner)
    except ValueError as exc:
        append_attempt_event(
            execution, attempt_id, "release-ambiguous",
            {"job_id": job_id, "reason": str(exc)}, actor=actor,
        )
        raise ValueError("submitted job could not be verified and remains quarantined") from exc
    append_attempt_event(
        execution, attempt_id, "job-verified",
        {"job_id": job_id, "job_name": row["JobName"], "state": row["JobState"]},
        actor=actor,
    )
    try:
        released = _run_with(
            runner, ["scontrol", "release", job_id], cwd=execution.directory
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        append_attempt_event(
            execution, attempt_id, "release-ambiguous",
            {"job_id": job_id, "reason": type(exc).__name__}, actor=actor,
        )
        raise ValueError("Slurm release outcome is ambiguous; do not submit again") from exc
    if released.returncode != 0:
        append_attempt_event(
            execution, attempt_id, "release-ambiguous",
            {"job_id": job_id, "return_code": released.returncode,
             "stderr": released.stderr[-1000:]}, actor=actor,
        )
        raise ValueError("Slurm release outcome is ambiguous; do not submit again")
    append_attempt_event(
        execution, attempt_id, "job-released", {"job_id": job_id}, actor=actor
    )
    return job_id


def _query_matching_jobs(
    execution: ManagedExecution, intent: dict[str, Any], *, runner: CommandRunner
) -> tuple[list[dict[str, str]], dict[str, str]]:
    token = intent["scheduler"]["job_name"]
    commands = {
        "squeue": ["squeue", "-h", "-o", "%A|%j|%a|%T|%r", "--name", token],
        "sacct": ["sacct", "-n", "-P", "-X", "--name", token,
                  "--format=JobIDRaw,JobName,State,ExitCode"],
    }
    raw: dict[str, str] = {}
    matches: dict[str, dict[str, str]] = {}
    for name, command in commands.items():
        result = _run_with(runner, command, cwd=execution.directory)
        if result.returncode:
            raise ValueError(f"{name} query failed during reconciliation")
        raw[name] = result.stdout
        for line in result.stdout.splitlines():
            values = line.split("|")
            if len(values) < 2:
                continue
            job_id, job_name = values[:2]
            parent = job_id.split("_", 1)[0].split(".", 1)[0]
            if job_name == token and JOB_ID_RE.fullmatch(parent):
                matches.setdefault(parent, {"job_id": parent, "job_name": job_name})
    return sorted(matches.values(), key=lambda row: int(row["job_id"])), raw


def _terminal_parent_from_sacct(
    text: str, *, job_id: str, job_name: str
) -> dict[str, str] | None:
    terminal = {"COMPLETED", *TERMINAL_FAILURE_STATES}
    matches: list[dict[str, str]] = []
    for line in text.splitlines():
        values = line.split("|")
        if len(values) < 4:
            continue
        observed_id, observed_name, state, exit_code = values[:4]
        if (
            observed_id == job_id
            and observed_name == job_name
            and base_slurm_state(state) in terminal
        ):
            matches.append(
                {"job_id": observed_id, "job_name": observed_name,
                 "state": state, "exit_code": exit_code}
            )
    if len(matches) > 1:
        raise ValueError("sacct returned duplicate exact array-parent rows")
    return matches[0] if matches else None


def reconcile_attempt(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    confirm_no_job: bool = False,
    rationale: str | None = None,
    runner: CommandRunner = _run,
    now_utc: str | None = None,
) -> str:
    intent = load_attempt_intent(execution, attempt_id)
    _verify_intent_hash(intent, intent_sha256)
    state = attempt_state(execution, attempt_id)
    recoverable_states = {
        "submission-ambiguous", "submitted-held", "verified-held",
        "release-ambiguous",
    }
    if state.status not in recoverable_states:
        raise ValueError("attempt is not in a recoverable scheduler state")
    matches, raw = _query_matching_jobs(execution, intent, runner=runner)
    observed_at = now_utc or utc_now()
    if len(matches) > 1:
        append_attempt_event(
            execution, attempt_id, "submission-permanently-ambiguous",
            {"match_count": len(matches), "job_ids": [row["job_id"] for row in matches],
             "squeue_psv": raw["squeue"], "sacct_psv": raw["sacct"]},
            actor=actor,
        )
        raise ValueError("multiple exact scheduler matches; attempt is permanently quarantined")
    if len(matches) == 1:
        job_id = matches[0]["job_id"]
        if state.job_id is not None and job_id != state.job_id:
            raise ValueError("scheduler match conflicts with the recorded job ID")
        require_held = state.status in {"submission-ambiguous", "submitted-held"}
        try:
            row = _validate_held_job(
                execution, intent, job_id, runner=runner,
                require_held=require_held,
            )
        except ValueError:
            previously_verified = any(
                event["event_type"] == "job-verified" for event in state.events
            )
            terminal_parent = _terminal_parent_from_sacct(
                raw["sacct"], job_id=job_id,
                job_name=intent["scheduler"]["job_name"],
            )
            if (
                state.status in {"verified-held", "release-ambiguous"}
                and previously_verified
                and state.job_id == job_id
                and terminal_parent is not None
            ):
                append_attempt_event(
                    execution, attempt_id, "job-released",
                    {
                        "job_id": job_id,
                        "reconciled": True,
                        "recovery_source": "terminal-sacct-parent",
                        "observed_state": terminal_parent["state"],
                        "observed_exit_code": terminal_parent["exit_code"],
                        "sacct_psv": raw["sacct"],
                    },
                    actor=actor,
                )
                return (
                    f"reconciled-terminal-sacct:{job_id}:"
                    f"{terminal_parent['state']}"
                )
            raise
        if state.status == "submission-ambiguous":
            append_attempt_event(
                execution, attempt_id, "submitted-held",
                {"job_id": job_id, "adopted": True,
                 "squeue_psv": raw["squeue"], "sacct_psv": raw["sacct"]}, actor=actor,
            )
            append_attempt_event(
                execution, attempt_id, "job-verified",
                {"job_id": job_id, "job_name": row["JobName"], "state": row["JobState"],
                 "reconciled": True}, actor=actor,
            )
        elif state.status == "submitted-held":
            append_attempt_event(
                execution, attempt_id, "job-verified",
                {"job_id": job_id, "job_name": row["JobName"],
                 "state": row["JobState"], "reconciled": True}, actor=actor,
            )
        held = (
            row.get("JobState") == "PENDING"
            and row.get("Reason") == "JobHeldUser"
        )
        if held:
            if state.status == "release-ambiguous":
                append_attempt_event(
                    execution, attempt_id, "job-verified",
                    {"job_id": job_id, "job_name": row["JobName"],
                     "state": row["JobState"], "reconciled": True}, actor=actor,
                )
            try:
                released = _run_with(
                    runner, ["scontrol", "release", job_id], cwd=execution.directory
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                append_attempt_event(
                    execution, attempt_id, "release-ambiguous",
                    {"job_id": job_id, "reason": type(exc).__name__,
                     "reconciled": True}, actor=actor,
                )
                raise ValueError("reconciled held job release remains ambiguous") from exc
            if released.returncode:
                append_attempt_event(
                    execution, attempt_id, "release-ambiguous",
                    {"job_id": job_id, "return_code": released.returncode,
                     "stderr": released.stderr[-1000:]}, actor=actor,
                )
                raise ValueError("reconciled held job release remains ambiguous")
        # If the same checksum-bound job is already running or terminal, the
        # original release succeeded.  Record that fact and never call sbatch.
        append_attempt_event(
            execution, attempt_id, "job-released",
            {"job_id": job_id, "reconciled": True,
             "observed_state": row.get("JobState")}, actor=actor,
        )
        return f"reconciled-same-job:{job_id}:{row.get('JobState', 'UNKNOWN')}"

    observation_payload = {
        "observed_at_utc": observed_at, "match_count": 0,
        "squeue_psv": raw["squeue"], "sacct_psv": raw["sacct"],
        "squeue_sha256": __import__("hashlib").sha256(raw["squeue"].encode()).hexdigest(),
        "sacct_sha256": __import__("hashlib").sha256(raw["sacct"].encode()).hexdigest(),
    }
    if state.status in {"submitted-held", "verified-held"}:
        append_attempt_event(
            execution, attempt_id, "release-ambiguous",
            {"job_id": state.job_id, "reason": "scheduler-zero-match",
             "reconciled": True}, actor=actor,
        )
        state = attempt_state(execution, attempt_id)
    if state.status == "release-ambiguous":
        append_attempt_event(
            execution, attempt_id, "release-scheduler-observation",
            {**observation_payload, "job_id": state.job_id}, actor=actor,
        )
        if confirm_no_job:
            raise ValueError("a known submitted job cannot be cleared as never submitted")
        return "release-zero-match-observation-recorded"
    append_attempt_event(
        execution, attempt_id, "scheduler-observation", observation_payload,
        actor=actor,
    )
    if not confirm_no_job:
        return "zero-match-observation-recorded"
    if not rationale or not rationale.strip():
        raise ValueError("confirm-no-job requires a non-empty rationale")
    observations = [
        event for event in attempt_state(execution, attempt_id).events
        if event["event_type"] == "scheduler-observation"
        and event["payload"].get("match_count") == 0
    ]
    if len(observations) < 2:
        raise ValueError("confirm-no-job requires two zero-match observations")
    timestamps = [
        datetime.fromisoformat(event["payload"]["observed_at_utc"])
        for event in observations[-2:]
    ]
    if (timestamps[1] - timestamps[0]).total_seconds() < 600:
        raise ValueError("zero-match observations must be at least 10 minutes apart")
    append_attempt_event(
        execution, attempt_id, "submission-not-found-confirmed",
        {"reviewer": actor, "rationale": rationale.strip(),
         "observation_event_hashes": [event["event_sha256"] for event in observations[-2:]]},
        actor=actor,
    )
    return "no-job-confirmed"


def _parse_sacct_rows(text: str) -> list[dict[str, str]]:
    names = ["job_id", "state", "exit_code", "elapsed_raw", "max_rss", "max_vmsize"]
    rows = []
    for raw in text.splitlines():
        values = raw.split("|")
        if len(values) == len(names) + 1 and values[-1] == "":
            values.pop()
        if len(values) != len(names):
            if raw.strip():
                raise ValueError(f"invalid sacct row: {raw!r}")
            continue
        rows.append(dict(zip(names, values)))
    return rows


def freeze_terminal_accounting(
    execution: ManagedExecution,
    *,
    attempt_id: str,
    intent_sha256: str,
    actor: str,
    runner: CommandRunner = _run,
) -> dict[str, Any]:
    intent = load_attempt_intent(execution, attempt_id)
    _verify_intent_hash(intent, intent_sha256)
    state = attempt_state(execution, attempt_id)
    if state.status != "released-active" or state.job_id is None:
        raise ValueError("terminal accounting requires one released attempt")
    job_id = state.job_id
    target = execution.directory / "attempts" / attempt_id / "accounting"
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            raise ValueError("terminal accounting path is an unsafe symlink")
        recovered = load_frozen_accounting(
            execution, attempt_id, require_event_binding=False
        )
        task_count = intent["selected"]["task_count"]
        expected_indices = {str(index) for index in range(1, task_count + 1)}
        if (
            recovered.get("execution_id") != execution.execution_id
            or recovered.get("execution_hash") != execution.execution_hash
            or recovered.get("job_id") != job_id
            or recovered.get("selected_task_count") != task_count
            or recovered.get("terminal_task_count") != task_count
            or recovered.get("accepted_terminal") is not True
            or set(require_dict(recovered, "task_states")) != expected_indices
            or set(require_dict(recovered, "task_exit_codes")) != expected_indices
        ):
            raise ValueError("existing terminal accounting cannot be recovered")
        append_attempt_event(
            execution, attempt_id, "terminal-accounting-frozen",
            {
                "job_id": job_id,
                "accounting_manifest_sha256": sha256_file(target / "SHA256SUMS"),
                "all_tasks_completed": recovered.get("all_tasks_completed"),
                "recovered_after_publish": True,
            },
            actor=actor,
        )
        return recovered
    sacct = _run_with(
        runner,
        ["sacct", "-n", "-P", "-j", job_id,
         "--format=JobIDRaw,State,ExitCode,ElapsedRaw,MaxRSS,MaxVMSize"],
        cwd=execution.directory,
    )
    squeue = _run_with(
        runner, ["squeue", "-h", "-o", "%A|%a|%T|%r", "-j", job_id],
        cwd=execution.directory,
    )
    if sacct.returncode or squeue.returncode:
        raise ValueError("cannot freeze incomplete scheduler accounting")
    if squeue.stdout.strip():
        raise ValueError("Slurm still reports active tasks; accounting is not terminal")
    rows = _parse_sacct_rows(sacct.stdout)
    task_count = intent["selected"]["task_count"]
    parent_rows = [row for row in rows if row["job_id"] == job_id]
    if len(parent_rows) != 1:
        raise ValueError("sacct lacks one exact array-parent accounting row")
    raw_task_rows = [
        row for row in rows
        if re.fullmatch(rf"{re.escape(job_id)}_[1-9][0-9]*", row["job_id"])
    ]
    task_rows = {
        int(row["job_id"].split("_", 1)[1]): row for row in raw_task_rows
    }
    if (
        len(raw_task_rows) != task_count
        or len(task_rows) != task_count
        or set(task_rows) != set(range(1, task_count + 1))
    ):
        raise ValueError("sacct lacks the exact array-task accounting set")
    terminal = {
        "COMPLETED", *(__import__("steel_module_production_phase2b_lib").TERMINAL_FAILURE_STATES)
    }
    if any(base_slurm_state(row["state"]) not in terminal for row in task_rows.values()):
        raise ValueError("sacct contains non-terminal array tasks")
    if base_slurm_state(parent_rows[0]["state"]) not in terminal:
        raise ValueError("sacct array parent is not terminal")
    all_completed = all(
        base_slurm_state(row["state"]) == "COMPLETED" and row["exit_code"] == "0:0"
        for row in task_rows.values()
    )
    payload = {
        "schema_version": ACCOUNTING_SCHEMA_VERSION,
        "created_at_utc": utc_now(), "created_by": actor,
        "execution_id": execution.execution_id, "execution_hash": execution.execution_hash,
        "attempt_id": attempt_id, "intent_sha256": intent_sha256, "job_id": job_id,
        "selected_task_count": task_count, "terminal_task_count": len(task_rows),
        "accepted_terminal": True, "all_tasks_completed": all_completed,
        "task_states": {str(index): task_rows[index]["state"] for index in sorted(task_rows)},
        "task_exit_codes": {
            str(index): task_rows[index]["exit_code"] for index in sorted(task_rows)
        },
    }
    temp = Path(tempfile.mkdtemp(prefix=".accounting-", dir=target.parent))
    try:
        (temp / "sacct.psv").write_text(sacct.stdout, encoding="utf-8")
        (temp / "squeue.psv").write_text(squeue.stdout, encoding="utf-8")
        (temp / "frozen.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (temp / "SHA256SUMS").write_text(
            "".join(
                f"{sha256_file(temp / name)}  {name}\n"
                for name in ("frozen.json", "sacct.psv", "squeue.psv")
            ), encoding="utf-8",
        )
        for path in temp.iterdir():
            path.chmod(0o444)
        fsync_tree(temp)
        with execution_lock(execution.directory):
            if target.exists() or target.is_symlink():
                raise ValueError("terminal accounting appeared concurrently")
            os.rename(temp, target)
            fsync_directory(target.parent)
        append_attempt_event(
            execution, attempt_id, "terminal-accounting-frozen",
            {"job_id": job_id, "accounting_manifest_sha256": sha256_file(target / "SHA256SUMS"),
             "all_tasks_completed": all_completed}, actor=actor,
        )
        return payload
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", allow_abbrev=False)

    prepare = sub.add_parser("prepare-intent", allow_abbrev=False)
    prepare.add_argument("--attempt-id", required=True)
    prepare.add_argument("--mode", required=True, choices=("initial", "resume", "retry-failed"))
    prepare_mode = prepare.add_mutually_exclusive_group(required=True)
    prepare_mode.add_argument("--check-only", action="store_true")
    prepare_mode.add_argument("--write-intent", action="store_true")
    prepare.add_argument("--actor", default=getpass.getuser())

    submit = sub.add_parser("submit-intent", allow_abbrev=False)
    submit.add_argument("--attempt-id", required=True)
    submit.add_argument("--intent-sha256", required=True)
    submit_mode = submit.add_mutually_exclusive_group(required=True)
    submit_mode.add_argument("--check-only", action="store_true")
    submit_mode.add_argument("--submit", action="store_true")
    submit.add_argument("--actor", default=getpass.getuser())

    cancel = sub.add_parser("cancel-intent", allow_abbrev=False)
    cancel.add_argument("--attempt-id", required=True)
    cancel.add_argument("--intent-sha256", required=True)
    cancel.add_argument("--actor", default=getpass.getuser())

    reconcile = sub.add_parser("reconcile", allow_abbrev=False)
    reconcile.add_argument("--attempt-id", required=True)
    reconcile.add_argument("--intent-sha256", required=True)
    reconcile.add_argument("--actor", default=getpass.getuser())
    reconcile.add_argument("--confirm-no-job", action="store_true")
    reconcile.add_argument("--rationale-file", type=Path)

    accounting = sub.add_parser("freeze-accounting", allow_abbrev=False)
    accounting.add_argument("--attempt-id", required=True)
    accounting.add_argument("--intent-sha256", required=True)
    accounting.add_argument("--actor", default=getpass.getuser())
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    try:
        readiness_path = repo_root / READINESS_LOCK_RELATIVE
        readiness = args.command != "status" or readiness_path.is_file()
        execution = _formal_execution(repo_root, readiness=readiness)
        if args.command == "status":
            print(f"execution_id: {execution.execution_id}")
            print(f"execution_hash: {execution.execution_hash}")
            print(f"submission_ready: {str(readiness).lower()}")
            print(f"intents: {len(list_attempt_ids(execution))}")
            for attempt_id in list_attempt_ids(execution):
                state = attempt_state(execution, attempt_id)
                print(f"{attempt_id}: {state.status} job={state.job_id or '-'}")
            return 0

        readiness_sha = _readiness_sha(repo_root)
        if args.command == "prepare-intent":
            value = prepare_attempt_intent(
                execution, attempt_id=args.attempt_id, mode=args.mode, actor=args.actor,
                write=args.write_intent, readiness_lock_sha256=readiness_sha,
            )
            print(f"intent: {'written' if args.write_intent else 'check-only'}")
            print(f"attempt_id: {value['attempt_id']}")
            print(f"intent_sha256: {value['intent_sha256']}")
            print(f"tasks: {value['selected']['task_count']}")
            print("No scheduler command was invoked.")
            return 0

        intent = load_attempt_intent(execution, args.attempt_id)
        _verify_intent_hash(intent, args.intent_sha256)
        if intent.get("readiness_lock_sha256") != readiness_sha:
            raise ValueError("intent readiness-lock digest is stale")
        if args.command == "submit-intent":
            if args.check_only:
                print("submit-intent check: PASS")
                print("command:", shlex.join(submission_command(execution, intent)))
                print("No scheduler command was invoked.")
            else:
                job_id = submit_intent(
                    execution, attempt_id=args.attempt_id,
                    intent_sha256=args.intent_sha256, actor=args.actor,
                )
                print(f"submitted and released held Slurm array: {job_id}")
            return 0
        if args.command == "cancel-intent":
            if attempt_state(execution, args.attempt_id).status != "prepared":
                raise ValueError("only a never-submitted prepared intent may be cancelled")
            append_attempt_event(
                execution, args.attempt_id, "intent-cancelled",
                {"reason": "explicit-user-cancellation"}, actor=args.actor,
            )
            print("intent cancelled; no scheduler command was invoked")
            return 0
        if args.command == "reconcile":
            rationale = None
            if args.rationale_file is not None:
                rationale = args.rationale_file.read_text(encoding="utf-8")
            result = reconcile_attempt(
                execution, attempt_id=args.attempt_id,
                intent_sha256=args.intent_sha256, actor=args.actor,
                confirm_no_job=args.confirm_no_job, rationale=rationale,
            )
            print(result)
            return 0
        if args.command == "freeze-accounting":
            value = freeze_terminal_accounting(
                execution, attempt_id=args.attempt_id,
                intent_sha256=args.intent_sha256, actor=args.actor,
            )
            print("terminal accounting frozen")
            print(f"job_id: {value['job_id']}")
            print(f"all_tasks_completed: {value['all_tasks_completed']}")
            return 0
        raise AssertionError("unreachable command")
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"Cannot manage steel-module production attempt: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
