#!/usr/bin/env python3
"""No-scheduler adversarial checks for the incident-bound BC-S1 successor.

The fixture deliberately seals a complete synthetic predecessor incident before
materializing the successor.  Synthetic evidence is never accepted statistical
evidence, but it has the same 32-task/8,000-event/64-seed shape and the same
cross-file accounting and event-chain contracts as the formal OSC incident.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import seal_steel_module_production_phase2b_incident as incident_lib
import steel_module_production_phase2b_lib as phase2b_lib
from check_steel_module_production_phase2b_control import (
    FakeSlurm,
    _fixture_sources,
    _prepare,
    make_execution,
)
from manage_steel_module_production_attempt import submit_intent
from seal_steel_module_production_phase2b_incident import (
    EXPECTED_FAILURE_LINE,
    publish_incident_bundle,
    validate_incident_bundle,
)
from steel_module_campaign_lib import canonical_json, sha256_bytes, sha256_file
from steel_module_production_checkpoint_lib import write_recursive_checksums
from steel_module_production_phase2b_lib import prepare_attempt_intent
from steel_module_production_program_lib import (
    seed_pair_registry_hash,
    seed_set_hash,
    task_set_hash,
)
from steel_module_production_successor_lib import (
    FORMAL_SUCCESSOR_EXECUTION_NAME,
    RECOVERY_READINESS_LOCK_RELATIVE,
    SUCCESSOR_EXECUTION_SCHEMA_VERSION,
    SUCCESSOR_OBJECT_KIND,
    load_execution_for_frozen_worker,
    load_successor_execution,
    materialize_successor_execution_companion,
    preview_successor_execution,
    successor_predecessor_retry_task_ids,
)


FIXTURE_READINESS = {
    "path": "/fixture/readiness.json",
    "sha256": "a" * 64,
    "schema_version": "fixture-readiness-v1",
    "status": "accepted-fixture",
    "implementation_commit": "f" * 40,
    "readiness_commit": "e" * 40,
}


def _tree_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    """Capture content and filesystem identity without following symlinks."""

    records: dict[str, dict[str, Any]] = {}
    paths = [root, *sorted(root.rglob("*"), key=lambda value: value.as_posix())]
    for path in paths:
        value = path.lstat()
        relative = "." if path == root else path.relative_to(root).as_posix()
        record: dict[str, Any] = {
            "mode": stat.S_IMODE(value.st_mode),
            "link_count": value.st_nlink,
            "size_bytes": value.st_size,
        }
        if stat.S_ISREG(value.st_mode):
            record.update(kind="file", sha256=sha256_file(path))
        elif stat.S_ISDIR(value.st_mode):
            record["kind"] = "directory"
        elif stat.S_ISLNK(value.st_mode):
            record.update(kind="symlink", target=os.readlink(path))
        else:
            record["kind"] = "other"
        records[relative] = record
    return records


def _contains_exact(value: object, expected: object) -> bool:
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(_contains_exact(child, expected) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_exact(child, expected) for child in value)
    return False


def _make_sealed_incident_fixture(
    repo_root: Path, scratch: Path
) -> tuple[object, Path, dict[str, Any]]:
    predecessor = make_execution(repo_root, scratch, "successor-predecessor")
    intent = _prepare(predecessor, "20260719T120000Z-initial")
    fake = FakeSlurm(
        predecessor, str(intent["attempt_id"]), mode="all-failed-accounting"
    )
    submit_intent(
        predecessor,
        attempt_id=str(intent["attempt_id"]),
        intent_sha256=str(intent["intent_sha256"]),
        actor="successor-fixture-reviewer",
        runner=fake,
    )
    attempt_dir = predecessor.directory / "attempts" / str(intent["attempt_id"])
    for index in range(1, 33):
        (attempt_dir / f"slurm-{fake.job_id}_{index}.out").write_text(
            EXPECTED_FAILURE_LINE + "\n", encoding="utf-8"
        )
    incident = publish_incident_bundle(
        predecessor,
        repo_root=repo_root,
        out_parent=scratch / "successor-incidents",
        attempt_id=str(intent["attempt_id"]),
        intent_sha256=str(intent["intent_sha256"]),
        actor="successor-fixture-reviewer",
        runner=fake,
        historical_readiness=FIXTURE_READINESS,
    )
    value = validate_incident_bundle(incident, execution=predecessor)
    assert value["accepted_incident_evidence"] is False
    assert value["simulation_disposition"]["events_consumed"] == 0
    assert value["simulation_disposition"]["production_seeds_consumed"] == 0
    assert value["scheduler"]["states"] == {"FAILED": 32}
    assert value["scheduler"]["exit_codes"] == {"1:0": 32}
    return predecessor, incident, value


def _successor_inputs(
    repo_root: Path,
    scratch: Path,
    predecessor: object,
    incident: Path,
) -> dict[str, Any]:
    source_root = scratch / "successor-source-inputs"
    source_root.mkdir()
    return {
        "repo_root": repo_root,
        "managed_child_dir": predecessor.managed_child.directory,
        "predecessor_dir": predecessor.directory,
        "incident_dir": incident,
        "pilot_dir": Path(predecessor.manifest["sealed_pilot"]["directory"]),
        "test_mode": True,
        "fixture_sources": _fixture_sources(source_root),
    }


def _scheduler_sentinels(
    scratch: Path,
) -> tuple[Path, dict[str, Path], str]:
    sentinel_bin = scratch / "successor-sentinel-bin"
    sentinel_bin.mkdir()
    markers: dict[str, Path] = {}
    for command in ("sbatch", "scontrol", "squeue", "sacct"):
        marker = scratch / f"forbidden-{command}-was-invoked"
        executable = sentinel_bin / command
        executable.write_text(
            "#!/usr/bin/env bash\n"
            f"printf invoked > {str(marker)!r}\n"
            "exit 97\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        markers[command] = marker
    original_path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(sentinel_bin) + os.pathsep + original_path
    return sentinel_bin, markers, original_path


def _assert_no_scheduler_contact(markers: dict[str, Path]) -> None:
    invoked = sorted(name for name, marker in markers.items() if marker.exists())
    assert not invoked, f"successor R2 contacted scheduler commands: {invoked}"


def _assert_successor_identity(
    successor: object,
    predecessor: object,
    incident_value: dict[str, Any],
) -> None:
    manifest = successor.manifest
    assert manifest["schema_version"] == SUCCESSOR_EXECUTION_SCHEMA_VERSION
    assert manifest["object_kind"] == SUCCESSOR_OBJECT_KIND
    assert manifest["test_mode"] is True
    assert manifest["accepted_statistical_evidence"] is False
    assert successor.execution_id != predecessor.execution_id
    assert successor.execution_hash != predecessor.execution_hash
    assert manifest["submission_policy"]["formal_initial_mode"] == (
        "predecessor-retry"
    )
    assert manifest["readiness_gate"]["tracked_path"] == (
        RECOVERY_READINESS_LOCK_RELATIVE.as_posix()
    )
    control_lock = manifest["artifacts"]["control_lock"]
    assert control_lock["protocol"] == phase2b_lib.CONTROL_LOCK_PROTOCOL_V2
    assert control_lock["content_token"] != predecessor.manifest["artifacts"][
        "control_lock"
    ].get("content_token")

    expected_authority = (
        predecessor.execution_id,
        predecessor.execution_hash,
        incident_value["incident_id"],
        incident_value["incident_hash"],
        incident_value["attempt"]["attempt_id"],
        incident_value["attempt"]["intent_sha256"],
        incident_value["attempt"]["job_id"],
        incident_value["attempt"]["event_chain_sha256"],
        incident_value["attempt"]["accounting_manifest_sha256"],
        incident_value["historical_readiness"]["sha256"],
    )
    for expected in expected_authority:
        assert _contains_exact(manifest, expected), (
            f"successor manifest omits predecessor authority {expected!r}"
        )


def _assert_exact_task_seed_reuse(successor: object, predecessor: object) -> None:
    parent_tasks = predecessor.managed_child.plan.tasks
    assert successor.tasks == predecessor.tasks == parent_tasks
    assert successor.scan_args == predecessor.scan_args
    assert (successor.directory / "tasks.tsv").read_bytes() == (
        predecessor.directory / "tasks.tsv"
    ).read_bytes()
    assert (successor.directory / "scan_args.txt").read_bytes() == (
        predecessor.directory / "scan_args.txt"
    ).read_bytes()
    seeds = tuple(seed for task in successor.tasks for seed in (task.seed1, task.seed2))
    parent_seeds = tuple(
        seed for task in parent_tasks for seed in (task.seed1, task.seed2)
    )
    assert len(seeds) == 64 and len(set(seeds)) == 64
    assert seeds == parent_seeds
    child = predecessor.managed_child.binding["child"]
    assert task_set_hash(successor.tasks) == child["task_set_hash"]
    assert seed_pair_registry_hash(successor.tasks) == child["task_seed_mapping_hash"]
    assert seed_set_hash(seeds) == child["seed_set_hash"]


def test_happy_path_and_r2_gate(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    predecessor, incident, incident_value = _make_sealed_incident_fixture(
        repo_root, scratch
    )
    inputs = _successor_inputs(repo_root, scratch, predecessor, incident)
    # A real recovery necessarily uses a newer control-plane commit than the
    # sealed predecessor incident.  Keep the simulation fixture unchanged but
    # make the successor control archive observably different so the happy path
    # proves that retry equivalence is physics-only, not control-source equality.
    successor_control = inputs["fixture_sources"][1]
    (successor_control / "recovery-control-marker.txt").write_text(
        "incident-bound successor control plane\n", encoding="utf-8"
    )
    target = scratch / "successor-execution"
    predecessor_before = _tree_snapshot(predecessor.directory)
    incident_before = _tree_snapshot(incident)
    _, markers, original_path = _scheduler_sentinels(scratch)
    try:
        preview = preview_successor_execution(**inputs)
        assert preview is not None
        assert not target.exists()
        _assert_no_scheduler_contact(markers)

        successor = materialize_successor_execution_companion(
            **inputs, out_dir=target
        )
        _assert_no_scheduler_contact(markers)
        assert successor.directory == target.resolve()
        assert target.stat().st_mode & 0o222 == 0
        for name in (
            "README.md", "STATIC_SHA256SUMS", "managed_execution.json",
            "scan_args.txt", "source_archives.json", "tasks.tsv",
        ):
            assert (target / name).stat().st_mode & 0o222 == 0
        assert not (target / "campaign.json").exists()
        for name in ("intents", "attempts", "finalized"):
            directory = target / name
            assert directory.is_dir() and not directory.is_symlink()
            assert not list(directory.iterdir())

        loaded = load_successor_execution(
            target,
            allow_test_mode=True,
            require_readiness=False,
            verify_live_predecessor=True,
        )
        _assert_successor_identity(loaded, predecessor, incident_value)
        _assert_exact_task_seed_reuse(loaded, predecessor)
        assert loaded.manifest["sources"]["simulation"] == (
            incident_value["source_identities"]["simulation"]
        )
        assert loaded.manifest["sources"]["control_plane"] != (
            incident_value["source_identities"]["control_plane"]
        )
        assert successor_predecessor_retry_task_ids(loaded) == tuple(
            task.logical_task_id for task in loaded.tasks
        )
        for mode in ("initial", "resume", "retry-failed"):
            try:
                phase2b_lib.selected_task_ids(loaded, mode)
            except ValueError:
                pass
            else:
                raise AssertionError(f"successor accepted first mode {mode}")
        try:
            load_execution_for_frozen_worker(target, control_root=repo_root)
        except ValueError:
            pass
        else:
            raise AssertionError("R2 successor worker bypassed additive readiness")
        assert _tree_snapshot(predecessor.directory) == predecessor_before
        assert _tree_snapshot(incident) == incident_before

        # The old predecessor readiness lock must not authorize this successor.
        try:
            load_successor_execution(
                target,
                allow_test_mode=True,
                require_readiness=True,
                verify_live_predecessor=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("R2 successor accepted predecessor readiness")

        # Direct library calls are part of the security boundary: R2 has no
        # intent even when a caller supplies a syntactically valid lock hash.
        for mode in ("initial", "resume", "retry-failed", "predecessor-retry"):
            try:
                prepare_attempt_intent(
                    loaded,
                    attempt_id=f"20260719T13000{len(mode) % 10}Z-{mode}",
                    mode=mode,
                    actor="successor-fixture-reviewer",
                    write=True,
                    readiness_lock_sha256="f" * 64,
                )
            except ValueError:
                pass
            else:
                raise AssertionError(f"R2 successor wrote a {mode} intent")
            assert not list((target / "intents").iterdir())
        _assert_no_scheduler_contact(markers)
    finally:
        os.environ["PATH"] = original_path


def _clone_rehashed_incident(
    incident: Path,
    parent: Path,
    label: str,
    mutate: Callable[[dict[str, Any]], None],
) -> Path:
    staging = parent / f"incident-policy-{label}-staging"
    shutil.copytree(incident, staging)
    for path in staging.rglob("*"):
        if path.is_file() and not path.is_symlink():
            path.chmod(0o644)
    value = json.loads((staging / "incident.json").read_text(encoding="utf-8"))
    mutate(value)
    value["incident_hash"] = incident_lib._incident_hash(value)
    value["incident_id"] = (
        "sm-v1-bc-s1-pre-simulation-incident-"
        f"{value['incident_hash'][:12]}"
    )
    (staging / "incident.json").write_text(
        json.dumps(value, indent=2) + "\n", encoding="utf-8"
    )
    checksum = staging / "SHA256SUMS"
    checksum.unlink()
    write_recursive_checksums(staging)
    target = parent / (
        f"bc-s1-{value['attempt']['job_id']}-{value['incident_hash'][:12]}"
    )
    staging.rename(target)
    # These mutations intentionally remain internally checksum-valid; the
    # successor policy, rather than a trivial checksum failure, must reject them.
    validate_incident_bundle(target)
    return target


def test_incident_authority_policy(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    predecessor, incident, _ = _make_sealed_incident_fixture(repo_root, scratch)
    inputs = _successor_inputs(repo_root, scratch, predecessor, incident)
    policy_parent = scratch / "successor-policy-incidents"
    policy_parent.mkdir()
    cases: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
        (
            "classification",
            lambda value: value.__setitem__("classification", "simulation-result"),
        ),
        (
            "events-consumed",
            lambda value: value["simulation_disposition"].__setitem__(
                "events_consumed", 1
            ),
        ),
        (
            "seeds-consumed",
            lambda value: value["simulation_disposition"].__setitem__(
                "production_seeds_consumed", 1
            ),
        ),
        (
            "geant4-invoked",
            lambda value: value["simulation_disposition"].__setitem__(
                "geant4_invoked", True
            ),
        ),
        (
            "task-output",
            lambda value: value["simulation_disposition"].__setitem__(
                "task_outputs_created", True
            ),
        ),
        (
            "seed-reuse-authority",
            lambda value: value["simulation_disposition"].__setitem__(
                "seed_reuse_requires_successor_authority", False
            ),
        ),
        (
            "seed-mapping",
            lambda value: value.__setitem__("task_seed_mapping_hash", "0" * 64),
        ),
        (
            "runtime",
            lambda value: value["runtime_identity"].__setitem__(
                "executable_sha256", "0" * 64
            ),
        ),
        (
            "predecessor-control-source",
            lambda value: value["source_identities"]["control_plane"].__setitem__(
                "tree_sha256", "0" * 64
            ),
        ),
    )
    for label, mutate in cases:
        tampered = _clone_rehashed_incident(
            incident, policy_parent, label, mutate
        )
        attempt_inputs = dict(inputs, incident_dir=tampered)
        try:
            preview_successor_execution(**attempt_inputs)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"successor accepted checksum-valid incident policy tamper: {label}"
            )
    assert not list((scratch / "successor-predecessor" / "execution" / "intents").glob("*successor*"))


def _refresh_successor_static_manifest(directory: Path) -> None:
    manifest_path = directory / "managed_execution.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["tasks"]["sha256"] = sha256_file(
        directory / "tasks.tsv"
    )
    manifest["artifacts"]["scan_args"]["sha256"] = sha256_file(
        directory / "scan_args.txt"
    )
    manifest["managed_child"]["tasks_sha256"] = sha256_file(
        directory / "tasks.tsv"
    )
    manifest["managed_child"]["scan_args_sha256"] = sha256_file(
        directory / "scan_args.txt"
    )
    manifest["execution_hash"] = phase2b_lib._semantic_hash(
        manifest, "execution_hash"
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    phase2b_lib._write_static_checksums(directory)
    for name in (
        "README.md", "STATIC_SHA256SUMS", "managed_execution.json",
        "scan_args.txt", "source_archives.json", "tasks.tsv",
    ):
        (directory / name).chmod(0o444)
    directory.chmod(0o555)


def _rewrite_task_rows(
    directory: Path, mutate: Callable[[list[dict[str, str]], list[str]], None]
) -> None:
    tasks_path = directory / "tasks.tsv"
    directory.chmod(0o755)
    for name in ("tasks.tsv", "scan_args.txt", "managed_execution.json", "STATIC_SHA256SUMS"):
        (directory / name).chmod(0o644)
    with tasks_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        rows = list(reader)
        fields = list(reader.fieldnames or ())
    scan_path = directory / "scan_args.txt"
    lines = scan_path.read_text(encoding="utf-8").splitlines()
    mutate(rows, lines)
    with tasks_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    scan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _refresh_successor_static_manifest(directory)


def test_exact_task_seed_reuse_and_binding(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    predecessor, incident, incident_value = _make_sealed_incident_fixture(
        repo_root, scratch
    )
    inputs = _successor_inputs(repo_root, scratch, predecessor, incident)
    original = materialize_successor_execution_companion(
        **inputs, out_dir=scratch / "successor-plan-original"
    )
    _assert_exact_task_seed_reuse(original, predecessor)

    def change_seed(rows: list[dict[str, str]], lines: list[str]) -> None:
        old = rows[0]["seed1"]
        replacement = str(int(old) + 1_000_003)
        rows[0]["seed1"] = replacement
        lines[0] = lines[0].replace(
            f"--seed1 {old}", f"--seed1 {replacement}", 1
        )

    def duplicate_seed(rows: list[dict[str, str]], lines: list[str]) -> None:
        old = rows[0]["seed1"]
        replacement = rows[1]["seed1"]
        rows[0]["seed1"] = replacement
        lines[0] = lines[0].replace(
            f"--seed1 {old}", f"--seed1 {replacement}", 1
        )

    def reorder(rows: list[dict[str, str]], lines: list[str]) -> None:
        rows[0], rows[1] = rows[1], rows[0]
        lines[0], lines[1] = lines[1], lines[0]

    for label, mutate in (
        ("changed-seed", change_seed),
        ("duplicate-seed", duplicate_seed),
        ("reordered-tasks", reorder),
    ):
        clone = scratch / f"successor-plan-{label}"
        shutil.copytree(original.directory, clone)
        _rewrite_task_rows(clone, mutate)
        try:
            load_successor_execution(
                clone,
                allow_test_mode=True,
                require_readiness=False,
                verify_live_predecessor=True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"successor accepted self-rehashed {label}")

    # Rebinding the successor manifest to another incident hash must not be
    # made valid merely by recomputing its local semantic/static hashes.
    rebound = scratch / "successor-rebound-incident"
    shutil.copytree(original.directory, rebound)
    rebound.chmod(0o755)
    manifest_path = rebound / "managed_execution.json"
    manifest_path.chmod(0o644)
    (rebound / "STATIC_SHA256SUMS").chmod(0o644)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    before = json.dumps(manifest)
    changed = before.replace(incident_value["incident_hash"], "b" * 64)
    assert changed != before
    manifest = json.loads(changed)
    manifest["execution_hash"] = phase2b_lib._semantic_hash(
        manifest, "execution_hash"
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    phase2b_lib._write_static_checksums(rebound)
    for name in (
        "README.md", "STATIC_SHA256SUMS", "managed_execution.json",
        "scan_args.txt", "source_archives.json", "tasks.tsv",
    ):
        (rebound / name).chmod(0o444)
    rebound.chmod(0o555)
    try:
        load_successor_execution(
            rebound,
            allow_test_mode=True,
            require_readiness=False,
            verify_live_predecessor=True,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("successor accepted a self-rehashed predecessor binding")

    deleted = scratch / "successor-deleted-authority-field"
    shutil.copytree(original.directory, deleted)
    deleted.chmod(0o755)
    deleted_manifest_path = deleted / "managed_execution.json"
    deleted_manifest_path.chmod(0o644)
    (deleted / "STATIC_SHA256SUMS").chmod(0o644)
    deleted_manifest = json.loads(deleted_manifest_path.read_text(encoding="utf-8"))
    del deleted_manifest["recovery_authority"]["predecessor_attempt"][
        "terminal_event_sha256"
    ]
    deleted_manifest["recovery_authority"]["authority_hash"] = (
        phase2b_lib._semantic_hash(
            deleted_manifest["recovery_authority"], "authority_hash"
        )
    )
    deleted_manifest["execution_hash"] = phase2b_lib._semantic_hash(
        deleted_manifest, "execution_hash"
    )
    deleted_manifest_path.write_text(
        json.dumps(deleted_manifest, indent=2) + "\n", encoding="utf-8"
    )
    phase2b_lib._write_static_checksums(deleted)
    for name in (
        "README.md", "STATIC_SHA256SUMS", "managed_execution.json",
        "scan_args.txt", "source_archives.json", "tasks.tsv",
    ):
        (deleted / name).chmod(0o444)
    deleted.chmod(0o555)
    try:
        load_successor_execution(
            deleted,
            allow_test_mode=True,
            require_readiness=False,
            verify_live_predecessor=True,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("successor accepted deleted attempt authority")


def test_atomicity_and_concurrency(repo_root: Path, scratch: Path) -> None:
    scratch.mkdir()
    predecessor, incident, _ = _make_sealed_incident_fixture(repo_root, scratch)
    inputs = _successor_inputs(repo_root, scratch, predecessor, incident)
    predecessor_before = _tree_snapshot(predecessor.directory)
    incident_before = _tree_snapshot(incident)

    for point in ("tasks", "predecessor", "sources", "checksums"):
        target = scratch / f"successor-fault-{point}"
        try:
            materialize_successor_execution_companion(
                **inputs, out_dir=target, fault_after=point
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"successor fault injection succeeded: {point}")
        assert not target.exists()
        assert not (target.parent / f".{target.name}.materialize.lock").exists()
        assert not list(target.parent.glob(f".{target.name}.tmp-*"))

    target = scratch / "successor-concurrent"

    def materialize() -> tuple[str, str]:
        try:
            value = materialize_successor_execution_companion(
                **inputs, out_dir=target
            )
        except ValueError as exc:
            return "rejected", str(exc)
        return "published", value.execution_hash

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: materialize(), range(2)))
    assert sum(kind == "published" for kind, _ in outcomes) == 1
    assert sum(kind == "rejected" for kind, _ in outcomes) == 1
    load_successor_execution(
        target,
        allow_test_mode=True,
        require_readiness=False,
        verify_live_predecessor=True,
    )
    assert not (target.parent / f".{target.name}.materialize.lock").exists()
    assert not list(target.parent.glob(f".{target.name}.tmp-*"))
    assert _tree_snapshot(predecessor.directory) == predecessor_before
    assert _tree_snapshot(incident) == incident_before

    existing = scratch / "successor-existing"
    existing.mkdir()
    try:
        materialize_successor_execution_companion(**inputs, out_dir=existing)
    except ValueError:
        pass
    else:
        raise AssertionError("successor materializer overwrote an existing target")

    escaped = incident / "forbidden-successor-child"
    try:
        materialize_successor_execution_companion(**inputs, out_dir=escaped)
    except ValueError:
        pass
    else:
        raise AssertionError("successor materializer wrote inside incident evidence")
    assert not escaped.exists()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    assert FORMAL_SUCCESSOR_EXECUTION_NAME != phase2b_lib.FORMAL_EXECUTION_NAME
    assert RECOVERY_READINESS_LOCK_RELATIVE != phase2b_lib.READINESS_LOCK_RELATIVE
    worker_source = (
        repo_root / "hpc/osc/run_steel_module_production_phase2b_task.py"
    ).read_text(encoding="utf-8")
    contract_source = (
        repo_root / "hpc/osc/steel_module_production_container_contract.py"
    ).read_text(encoding="utf-8")
    for fragment in (
        "build_production_apptainer_prefix",
        "production_apptainer_host_environment",
        "AdditionalBind(task_root, container_task_root, writable=True)",
        "cwd=control_source",
    ):
        assert fragment in worker_source, fragment
    for fragment in (
        'NO_MOUNT_CLASSES = "hostfs,cwd,bind-paths"',
        '"--cleanenv"',
        '"--containall"',
        '"--no-home"',
        'CONTAINER_EXECUTION_ROOT',
        'writable=False',
    ):
        assert fragment in contract_source, fragment
    with tempfile.TemporaryDirectory(
        prefix="steel-module-production-successor-"
    ) as temporary:
        scratch = Path(temporary)
        _, markers, original_path = _scheduler_sentinels(scratch)
        try:
            test_happy_path_and_r2_gate(repo_root, scratch / "happy")
            test_incident_authority_policy(repo_root, scratch / "authority")
            test_exact_task_seed_reuse_and_binding(repo_root, scratch / "plan")
            test_atomicity_and_concurrency(repo_root, scratch / "atomic")
            _assert_no_scheduler_contact(markers)
        finally:
            os.environ["PATH"] = original_path
    print("steel-module production successor R2: PASS")
    print("scheduler: forbidden command sentinels; real Slurm calls: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
