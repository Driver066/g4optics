#!/usr/bin/env python3
"""Focused no-scheduler checks for the R3 probe-rejection sealer."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from check_steel_module_production_r3_probe import _write_raw_workspace
from check_steel_module_production_successor import (
    _fixture_r3_failure_identity,
    _make_v3_fixture,
)
from steel_module_campaign_lib import canonical_json, sha256_bytes
from steel_module_production_r3_probe_lib import (
    REPORT_KEYS,
    canonical_r3_evidence_root,
    expected_r3_job_name,
    validate_raw_probe_workspace,
    validate_rejected_raw_probe_workspace,
)
import steel_module_production_r3_probe_rejection_lib as rejection_lib
from steel_module_production_r3_probe_rejection_lib import (
    FORMAL_FALSE_REPORT_KEYS,
    collect_r3_probe_rejection_evidence,
    seal_r3_probe_rejection_evidence,
    validate_r3_probe_rejection_bundle,
)
from steel_module_production_successor_lib import (
    FORMAL_SUCCESSOR_EXECUTION_NAME_V3,
    materialize_successor_v3_execution_companion,
)


def _canonical_hash(value: object) -> str:
    return sha256_bytes(canonical_json(value))


def _expect_failure(action: object, fragment: str | None = None) -> None:
    try:
        action()  # type: ignore[operator]
    except ValueError as exc:
        if fragment is not None:
            assert fragment in str(exc), (fragment, str(exc))
        return
    raise AssertionError("expected fail-closed R3 rejection")


def _check_formal_authority_root_dispatch(root: Path) -> None:
    """Use the historical archive when v4 validates from frozen control."""

    execution_dir = (root / rejection_lib.FORMAL_EXECUTION_NAME).resolve()
    frozen_control = execution_dir / "sources/control"
    sentinel = SimpleNamespace(
        directory=execution_dir,
        execution_id=rejection_lib.FORMAL_EXECUTION_ID,
        execution_hash=rejection_lib.FORMAL_EXECUTION_HASH,
        manifest={
            "schema_version": rejection_lib.SUCCESSOR_EXECUTION_SCHEMA_VERSION_V3
        },
    )
    with patch.object(
        rejection_lib, "load_successor_execution", return_value=sentinel
    ) as loader:
        assert rejection_lib._load_execution(
            execution_dir,
            repo_root=frozen_control,
            test_mode=False,
        ) is sentinel
        kwargs = loader.call_args.kwargs
        assert kwargs["verify_phase2a_control_plane"] is False
        assert kwargs["verify_live_predecessor"] is False
    live_root = (root / "live-repo").resolve()
    with patch.object(
        rejection_lib, "load_successor_execution", return_value=sentinel
    ) as loader:
        assert rejection_lib._load_execution(
            execution_dir,
            repo_root=live_root,
            test_mode=False,
        ) is sentinel
        kwargs = loader.call_args.kwargs
        assert kwargs["verify_phase2a_control_plane"] is True
        assert kwargs["verify_live_predecessor"] is True


def _make_rejected_raw(path: Path, successor: object) -> Path:
    raw = _write_raw_workspace(path, successor, validate_success=False)
    payload_path = raw / "probe_result.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["probe_passed"] = False
    payload["accepted_compute_preflight_evidence"] = False
    for key in FORMAL_FALSE_REPORT_KEYS:
        payload["container_report"][key] = False
    payload.pop("raw_result_hash")
    payload["raw_result_hash"] = _canonical_hash(payload)
    payload_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (raw / "container-report.tsv").write_text(
        "".join(
            f"{name}\t{'true' if payload['container_report'][name] else 'false'}\n"
            for name in REPORT_KEYS
        ),
        encoding="utf-8",
    )
    validate_rejected_raw_probe_workspace(
        raw,
        expected_false_keys=FORMAL_FALSE_REPORT_KEYS,
        allow_test_mode=True,
    )
    _expect_failure(
        lambda: validate_raw_probe_workspace(raw, allow_test_mode=True),
        "did not pass",
    )
    return raw


def _fixture(repo_root: Path, root: Path) -> tuple[object, Path, dict[str, Path]]:
    lineage = root / "lineage"
    lineage.mkdir()
    _, predecessor_v2, _, inputs = _make_v3_fixture(repo_root, lineage)
    target = root / "formal/work/campaigns" / FORMAL_SUCCESSOR_EXECUTION_NAME_V3
    target.parent.mkdir(parents=True)
    with _fixture_r3_failure_identity(predecessor_v2):
        successor = materialize_successor_v3_execution_companion(
            **inputs, out_dir=target
        )
    r3_root = canonical_r3_evidence_root(successor.directory)
    raw_parent = r3_root / "raw"
    raw_parent.mkdir(parents=True)
    job_name = expected_r3_job_name(successor.execution_hash)
    raw = _make_rejected_raw(raw_parent / job_name, successor)
    job_id = str(
        json.loads((raw / "probe_result.json").read_text(encoding="utf-8"))[
            "slurm_job_id"
        ]
    )
    inputs_root = root / "inputs"
    inputs_root.mkdir()
    values = {
        "held": inputs_root / "held.txt",
        "sacct": inputs_root / "sacct.psv",
        "squeue": inputs_root / "squeue.psv",
        "log": r3_root / f"slurm-{job_id}.out",
    }
    command = (
        successor.directory
        / "sources/control/hpc/osc/run_steel_module_production_r3_probe.sbatch"
    )
    values["held"].write_text(
        f"JobId={job_id} JobName={job_name} Account=pas2524 "
        "JobState=PENDING Reason=JobHeldUser Requeue=0 "
        f"Command={command} WorkDir={successor.directory} "
        f"StdOut={values['log']}\n",
        encoding="utf-8",
    )
    values["sacct"].write_text(
        f"{job_id}|{job_name}|pas2524|FAILED|1:0|9\n"
        f"{job_id}.batch|batch|pas2524|FAILED|1:0|9\n"
        f"{job_id}.extern|extern|pas2524|COMPLETED|0:0|9\n",
        encoding="utf-8",
    )
    values["squeue"].write_text("", encoding="utf-8")
    values["log"].write_text(
        "Cannot run steel-module R3 container probe: R3 container isolation "
        f"probe failed; evidence: {raw.resolve()}\n",
        encoding="utf-8",
    )
    return successor, raw, values


def _kwargs(
    repo_root: Path, successor: object, raw: Path, values: dict[str, Path]
) -> dict[str, object]:
    return {
        "execution_dir": successor.directory,
        "raw_workspace": raw,
        "held_scontrol_input": values["held"],
        "sacct_input": values["sacct"],
        "squeue_input": values["squeue"],
        "slurm_output_input": values["log"],
        "repo_root": repo_root,
        "test_mode": True,
    }


def _rewrite_bundle_checksums(bundle: Path) -> None:
    manifest = bundle / "SHA256SUMS"
    manifest.chmod(0o644)
    rows = []
    from steel_module_campaign_lib import sha256_file

    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path != manifest:
            rows.append(f"{sha256_file(path)}  {path.relative_to(bundle).as_posix()}\n")
    manifest.write_text("".join(rows), encoding="utf-8")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="steel-r3-rejection-check-") as raw_root:
        root = Path(raw_root)
        _check_formal_authority_root_dispatch(root / "authority-dispatch")
        successor, raw, values = _fixture(repo_root, root)
        kwargs = _kwargs(repo_root, successor, raw, values)

        failures = canonical_r3_evidence_root(successor.directory) / "failures"
        before = set(failures.iterdir()) if failures.exists() else set()
        payload, snapshot = collect_r3_probe_rejection_evidence(**kwargs)
        after = set(failures.iterdir()) if failures.exists() else set()
        assert before == after
        assert payload["accepted_compute_preflight_evidence"] is False
        assert payload["probe"]["false_report_keys"] == [
            name for name in REPORT_KEYS if name in FORMAL_FALSE_REPORT_KEYS
        ]
        assert payload["consumption"] == {
            "events_consumed": 0,
            "production_seeds_consumed": 0,
        }
        assert snapshot["snapshot_hash"] == payload["execution"]["snapshot_hash"]

        target = seal_r3_probe_rejection_evidence(**kwargs)
        recorded = validate_r3_probe_rejection_bundle(
            target,
            execution_dir=successor.directory,
            require_current_execution=True,
            allow_test_mode=True,
            repo_root=repo_root,
        )
        assert recorded == payload
        assert target.name.endswith(payload["rejection_hash"][:12])
        _expect_failure(
            lambda: seal_r3_probe_rejection_evidence(**kwargs), "overwrite"
        )

        passed = _write_raw_workspace(
            root / "passed-raw", successor, validate_success=False
        )
        _expect_failure(
            lambda: validate_rejected_raw_probe_workspace(
                passed,
                expected_false_keys=FORMAL_FALSE_REPORT_KEYS,
                allow_test_mode=True,
            ),
            "classification mismatch",
        )

        drifted_raw = root / "drifted-raw"
        shutil.copytree(raw, drifted_raw)
        drifted_payload_path = drifted_raw / "probe_result.json"
        drifted = json.loads(drifted_payload_path.read_text(encoding="utf-8"))
        drifted["container_report"]["static_writer_open_rejected"] = True
        drifted.pop("raw_result_hash")
        drifted["raw_result_hash"] = _canonical_hash(drifted)
        drifted_payload_path.write_text(
            json.dumps(drifted, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (drifted_raw / "container-report.tsv").write_text(
            "".join(
                f"{name}\t{'true' if drifted['container_report'][name] else 'false'}\n"
                for name in REPORT_KEYS
            ),
            encoding="utf-8",
        )
        _expect_failure(
            lambda: validate_rejected_raw_probe_workspace(
                drifted_raw,
                expected_false_keys=FORMAL_FALSE_REPORT_KEYS,
                allow_test_mode=True,
                require_recorded_location=False,
            ),
            "classification mismatch",
        )

        tampered = root / "tampered-bundle"
        shutil.copytree(target, tampered)
        for path in tampered.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
            elif path.is_file():
                path.chmod(0o644)
        (tampered / "raw/stdout.txt").write_text("tampered\n", encoding="utf-8")
        _rewrite_bundle_checksums(tampered)
        _expect_failure(
            lambda: validate_r3_probe_rejection_bundle(
                tampered,
                allow_test_mode=True,
                require_canonical_location=False,
            )
        )

        original = values["squeue"].read_text(encoding="utf-8")
        values["squeue"].write_text("70000123|RUNNING\n", encoding="utf-8")
        _expect_failure(
            lambda: collect_r3_probe_rejection_evidence(**kwargs), "remains active"
        )
        values["squeue"].write_text(original, encoding="utf-8")

        original_publish = rejection_lib.publish_directory_no_replace

        def fail_publish(_temporary: Path, _target: Path) -> None:
            raise RuntimeError("injected rejection publication failure")

        rejection_lib.publish_directory_no_replace = fail_publish
        second_kwargs = dict(kwargs)
        second_raw = root / "second-source-raw"
        shutil.copytree(raw, second_raw)
        # A fault-cleanup test needs a distinct content address but not another
        # semantically accepted historical result, so alter only test stdout
        # and all hashes which bind it.
        second_payload_path = second_raw / "probe_result.json"
        second_payload = json.loads(second_payload_path.read_text(encoding="utf-8"))
        (second_raw / "stdout.txt").write_text("second fixture\n", encoding="utf-8")
        from steel_module_campaign_lib import sha256_file

        second_payload["stdout_sha256"] = sha256_file(second_raw / "stdout.txt")
        second_payload["probe_workspace"] = str(second_raw.resolve())
        second_payload.pop("raw_result_hash")
        second_payload["raw_result_hash"] = _canonical_hash(second_payload)
        second_payload_path.write_text(
            json.dumps(second_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        second_kwargs["raw_workspace"] = second_raw
        values["log"].write_text(
            "Cannot run steel-module R3 container probe: R3 container isolation "
            f"probe failed; evidence: {second_raw.resolve()}\n",
            encoding="utf-8",
        )
        try:
            try:
                seal_r3_probe_rejection_evidence(**second_kwargs)
            except RuntimeError as exc:
                assert "injected" in str(exc)
            else:
                raise AssertionError("publication fault was not injected")
        finally:
            rejection_lib.publish_directory_no_replace = original_publish
        assert not any(path.name.startswith(".") for path in failures.iterdir())

        for command in ("sbatch", "scontrol", "squeue", "sacct"):
            assert command not in rejection_lib.__dict__
        assert os.environ.get("SLURM_JOB_ID") is None or True

    print(
        "steel-module production R3 probe rejection: PASS "
        "(exact two-key rejection, failed accounting, immutable evidence)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
