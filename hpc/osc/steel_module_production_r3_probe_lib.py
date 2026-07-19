#!/usr/bin/env python3
"""R3 compute-node container-isolation probe and evidence primitives.

Nothing in this module submits, releases, cancels, or queries a scheduler.  A
probe is run inside an already allocated compute job.  Terminal ``sacct`` and
``squeue`` text is supplied later through a checksum-bound input interface.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from steel_module_campaign_lib import (
    canonical_json,
    load_json,
    resolve_recorded_artifact,
    sha256_bytes,
    sha256_file,
)
from steel_module_production_checkpoint_lib import (
    publish_directory_no_replace,
    recursive_file_records,
    verify_recursive_checksums,
    write_recursive_checksums,
)
from steel_module_production_container_contract import (
    AdditionalBind,
    CONTAINER_EXECUTION_ROOT,
    NO_MOUNT_CLASSES,
    ProductionContainerInputs,
    build_production_apptainer_prefix,
    inspect_apptainer_runtime_identity,
    production_apptainer_host_environment,
)
from steel_module_production_successor_lib import (
    SUCCESSOR_EXECUTION_SCHEMA_VERSION,
    load_successor_execution,
    validate_successor_r2_boundary,
)


RAW_PROBE_SCHEMA_VERSION = "steel-module-production-r3-container-probe-raw-v2"
ACCOUNTING_INPUT_SCHEMA_VERSION = (
    "steel-module-production-r3-terminal-accounting-input-v2"
)
EVIDENCE_SCHEMA_VERSION = "steel-module-production-r3-container-probe-evidence-v2"
CONTAINER_PROBE_PREFIX = CONTAINER_EXECUTION_ROOT + "/attempts/.r3-probe-"
FORMAL_ACCOUNT = "PAS2524"
JOB_ID_RE = re.compile(r"^[1-9][0-9]*$")
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

REPORT_KEYS = (
    "mountinfo_execution_alias_read_only",
    "mountinfo_probe_leaf_read_write",
    "mountinfo_no_other_execution_rw_submount",
    "execution_root_create_rejected",
    "static_writer_open_rejected",
    "control_lock_writer_open_rejected",
    "intents_create_rejected",
    "attempts_create_rejected",
    "finalized_create_rejected",
    "original_path_hidden",
    "original_path_create_rejected",
    "adjacent_create_rejected",
    "challenge_hash_matches",
    "roundtrip_written",
)

RAW_WORKSPACE_FILES = frozenset(
    {
        "challenge.bin",
        "container-report.tsv",
        "container-roundtrip.bin",
        "probe_result.json",
        "stderr.txt",
        "stdout.txt",
    }
)


CONTAINER_PROBE_SCRIPT = r"""
set -euo pipefail
execution_root="$1"; original_root="$2"; probe_root="$3"
adjacent_root="$4"; token="$5"; expected_sha="$6"
report="${probe_root}/container-report.tsv"
roundtrip="${probe_root}/container-roundtrip.bin"

# Verify the kernel's final mount topology before attempting any negative
# write.  This prevents a mis-built probe from touching production state.
mount_rows() { awk -v target="$1" '$5 == target { count += 1 } END { print count + 0 }' /proc/self/mountinfo; }
mount_options() {
  awk -v target="$1" '$5 == target {
    separator = 0
    for (i = 7; i <= NF; i += 1) if ($i == "-") { separator = i; break }
    if (separator == 0 || separator + 3 > NF) exit 2
    print $6 "|" $(separator + 3)
  }' /proc/self/mountinfo
}
has_option() {
  options="${1//|/,}"
  case ",${options}," in *",$2,"*) return 0;; *) return 1;; esac
}

[[ "$(mount_rows "${execution_root}")" == 1 ]]
[[ "$(mount_rows "${probe_root}")" == 1 ]]
execution_options="$(mount_options "${execution_root}")"
probe_options="$(mount_options "${probe_root}")"
has_option "${execution_options}" ro
has_option "${probe_options}" rw
other_rw="$(awk -v parent="${execution_root}/" -v probe="${probe_root}" '
    index($5, parent) == 1 && $5 != probe {
      separator = 0
      for (i = 7; i <= NF; i += 1) if ($i == "-") { separator = i; break }
      mount_options = "," $6 ","
      super_options = separator > 0 && separator + 3 <= NF ? "," $(separator + 3) "," : ""
      if (index(mount_options, ",rw,") || index(super_options, ",rw,")) count += 1
    }
    END { print count + 0 }
  ' /proc/self/mountinfo)"
[[ "${other_rw}" == 0 ]]

bool_row() { printf '%s\t%s\n' "$1" "$2" >> "${report}"; }

create_rejected() {
  target="$1"
  if (umask 077; : > "${target}") 2>/dev/null; then
    rm -f -- "${target}" 2>/dev/null || true
    return 1
  fi
  return 0
}

writer_open_rejected() {
  target="$1"
  if (exec 9>>"${target}"; exec 9>&-) 2>/dev/null; then return 1; fi
  return 0
}

directory_create_rejected() {
  target="$1"
  if mkdir -- "${target}" 2>/dev/null; then
    rmdir -- "${target}" 2>/dev/null || true
    return 1
  fi
  return 0
}

: > "${report}"
bool_row mountinfo_execution_alias_read_only true
bool_row mountinfo_probe_leaf_read_write true
bool_row mountinfo_no_other_execution_rw_submount true
if create_rejected "${execution_root}/.r3-${token}"; then
  bool_row execution_root_create_rejected true
else bool_row execution_root_create_rejected false; fi
if writer_open_rejected "${execution_root}/managed_execution.json"; then
  bool_row static_writer_open_rejected true
else bool_row static_writer_open_rejected false; fi
if writer_open_rejected "${execution_root}/.control.lock"; then
  bool_row control_lock_writer_open_rejected true
else bool_row control_lock_writer_open_rejected false; fi
for root_name in intents attempts finalized; do
  if create_rejected "${execution_root}/${root_name}/.r3-${token}"; then value=true
  else value=false; fi
  bool_row "${root_name}_create_rejected" "${value}"
done
if [[ ! -e "${original_root}" ]]; then
  bool_row original_path_hidden true
else bool_row original_path_hidden false; fi
if create_rejected "${original_root}/.r3-${token}"; then
  bool_row original_path_create_rejected true
else bool_row original_path_create_rejected false; fi
if directory_create_rejected "${adjacent_root}"; then
  bool_row adjacent_create_rejected true
else bool_row adjacent_create_rejected false; fi

actual_sha="$(sha256sum "${probe_root}/challenge.bin" | awk '{print $1}')"
if [[ "${actual_sha}" == "${expected_sha}" ]]; then
  bool_row challenge_hash_matches true
else bool_row challenge_hash_matches false; fi
cp -- "${probe_root}/challenge.bin" "${roundtrip}"
if [[ -f "${roundtrip}" ]]; then bool_row roundtrip_written true
else bool_row roundtrip_written false; fi
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_hash(value: object) -> str:
    return sha256_bytes(canonical_json(value))


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _write_exclusive_json(path: Path, value: object) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o444)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _write_exclusive_text(path: Path, text: str, *, mode: int = 0o444) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, mode)
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


def canonical_r3_evidence_root(execution_dir: Path) -> Path:
    execution = execution_dir.expanduser().resolve()
    if execution.parent.name != "campaigns":
        raise ValueError("formal R3 execution is not under the canonical campaigns root")
    return execution.parent.parent / "evidence" / "steel-module-production-r3"


def _safe_new_workspace(path: Path, *, execution_dir: Path) -> Path:
    requested = path.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError("probe workspace and parent must not be symlinks")
    parent = requested.parent.resolve()
    workspace = parent / requested.name
    execution = execution_dir.resolve()
    expected_parent = canonical_r3_evidence_root(execution) / "raw"
    if (
        not parent.is_dir()
        or parent != expected_parent
        or workspace == execution
        or workspace in execution.parents
        or execution in workspace.parents
        or workspace.exists()
        or workspace.is_symlink()
    ):
        raise ValueError("probe workspace is missing, overlapping, or already exists")
    adjacent = workspace.with_name(workspace.name + "-adjacent")
    if adjacent.exists() or adjacent.is_symlink():
        raise ValueError("probe adjacent-sibling sentinel already exists")
    workspace.mkdir(mode=0o700)
    return workspace


def _parse_container_report(path: Path) -> dict[str, bool]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("container probe did not create a regular report")
    values: dict[str, bool] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        pieces = raw.split("\t")
        if len(pieces) != 2 or pieces[0] in values or pieces[1] not in {"true", "false"}:
            raise ValueError(f"invalid container report row {number}: {raw!r}")
        values[pieces[0]] = pieces[1] == "true"
    if set(values) != set(REPORT_KEYS):
        raise ValueError("container report key set mismatch")
    return values


def _execution_snapshot(execution_dir: Path) -> tuple[dict[str, str], str]:
    records = recursive_file_records(execution_dir, exclude=())
    return records, _canonical_hash(records)


def expected_r3_job_name(execution_hash: str) -> str:
    if not _is_sha256(execution_hash):
        raise ValueError("R3 job-name derivation requires an execution SHA-256")
    return "g4sm-r3-" + execution_hash[:12]


def _slurm_identity(
    environment: Mapping[str, str], *, expected_job_name: str
) -> tuple[str, str]:
    job_id = environment.get("SLURM_JOB_ID", "")
    job_name = environment.get("SLURM_JOB_NAME", "")
    if not JOB_ID_RE.fullmatch(job_id) or job_name != expected_job_name:
        raise ValueError("formal R3 probe must run inside one identified Slurm job")
    if environment.get("SLURM_ARRAY_JOB_ID") or environment.get("SLURM_ARRAY_TASK_ID"):
        raise ValueError("formal R3 probe must be a single non-array job")
    if not environment.get("SLURMD_NODENAME"):
        raise ValueError("formal R3 probe lacks compute-node identity")
    return job_id, job_name


def run_r3_container_probe(
    *,
    execution_dir: Path,
    control_root: Path,
    workspace: Path,
    host_environment: Mapping[str, str] | None = None,
    apptainer_path: Path | None = None,
) -> Path:
    """Run one production-like isolation probe without invoking Geant4."""

    environment_source = os.environ if host_environment is None else host_environment
    clean_environment = production_apptainer_host_environment(environment_source)
    execution = load_successor_execution(
        execution_dir,
        repo_root=control_root,
        require_readiness=False,
        verify_runtime=True,
        verify_phase2a_control_plane=False,
        verify_live_predecessor=False,
    )
    if execution.manifest.get("schema_version") != SUCCESSOR_EXECUTION_SCHEMA_VERSION:
        raise ValueError("R3 probe requires the incident-bound successor execution")
    validate_successor_r2_boundary(execution, repo_root=control_root)
    job_id, job_name = _slurm_identity(
        environment_source,
        expected_job_name=expected_r3_job_name(execution.execution_hash),
    )
    execution_dir = execution.directory
    workspace = _safe_new_workspace(workspace, execution_dir=execution_dir)
    adjacent = workspace.with_name(workspace.name + "-adjacent")
    token = secrets.token_hex(16)
    if not TOKEN_RE.fullmatch(token):
        raise RuntimeError("internal probe token generation failed")
    challenge = workspace / "challenge.bin"
    challenge.write_bytes(secrets.token_bytes(64))
    challenge.chmod(0o444)
    challenge_sha = sha256_file(challenge)

    before_records, before_hash = _execution_snapshot(execution_dir)
    sources = execution.manifest["sources"]
    simulation_root = execution_dir / sources["simulation"]["path"]
    control_source = execution_dir / sources["control_plane"]["path"]
    environment = execution.environment
    image, image_sha = resolve_recorded_artifact(environment["image"], "Apptainer image")
    _, data_manifest_sha = resolve_recorded_artifact(
        environment["g4_data_manifest"], "Geant4 data manifest"
    )
    executable, executable_sha = resolve_recorded_artifact(
        environment["build_artifact"], "prebuilt executable"
    )
    lock_record = execution.manifest["artifacts"]["control_lock"]
    lock_path = execution_dir / lock_record["path"]
    lock_stat = lock_path.lstat()
    lock_diagnostics = {
        "protocol": lock_record["protocol"],
        "creation_device": lock_record["creation_device"],
        "creation_inode": lock_record["creation_inode"],
        "compute_device": lock_stat.st_dev,
        "compute_inode": lock_stat.st_ino,
        "mode": lock_stat.st_mode & 0o777,
        "size_bytes": lock_stat.st_size,
        "sha256": sha256_file(lock_path),
        "link_count": lock_stat.st_nlink,
        "cross_node_numeric_identity_required": False,
        "portable_identity_matches": (
            lock_stat.st_mode & 0o777 == lock_record["mode"]
            and lock_stat.st_size == lock_record["size_bytes"]
            and sha256_file(lock_path) == lock_record["sha256"]
            and lock_stat.st_nlink == lock_record["link_count"] == 1
        ),
    }
    data_root = Path.home() / "geant4-data" / "11.4.2"
    if not data_root.is_dir() or data_root.is_symlink():
        raise ValueError(f"missing canonical Geant4 data root: {data_root}")
    selected_apptainer = apptainer_path
    if selected_apptainer is None:
        raw_apptainer = shutil.which("apptainer")
        if raw_apptainer is None:
            raise ValueError("apptainer is not available")
        selected_apptainer = Path(raw_apptainer)
    engine_identity = inspect_apptainer_runtime_identity(
        selected_apptainer,
        environment=clean_environment,
        cwd=control_source,
    )
    selected_apptainer = Path(engine_identity["apptainer_path"])
    container_probe_root = CONTAINER_PROBE_PREFIX + token
    container_adjacent = CONTAINER_PROBE_PREFIX + "adjacent-" + token
    prefix = build_production_apptainer_prefix(
        apptainer=selected_apptainer,
        inputs=ProductionContainerInputs(
            image=image,
            simulation_root=simulation_root,
            control_root=control_source,
            execution_root=execution_dir,
            data_root=data_root,
            executable_directory=executable.parent,
        ),
        additional_binds=(
            AdditionalBind(workspace, container_probe_root, writable=True),
        ),
    )
    command = [
        *prefix,
        "bash",
        "-lc",
        CONTAINER_PROBE_SCRIPT,
        "bash",
        CONTAINER_EXECUTION_ROOT,
        str(execution_dir),
        container_probe_root,
        container_adjacent,
        token,
        challenge_sha,
    ]
    result = subprocess.run(
        command,
        cwd=control_source,
        env=clean_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    _write_exclusive_text(workspace / "stdout.txt", result.stdout)
    _write_exclusive_text(workspace / "stderr.txt", result.stderr)
    after_records, after_hash = _execution_snapshot(execution_dir)
    report = _parse_container_report(workspace / "container-report.tsv")
    roundtrip = workspace / "container-roundtrip.bin"
    roundtrip_sha = sha256_file(roundtrip) if roundtrip.is_file() else None
    workspace_files_before_result = {
        path.name for path in workspace.iterdir() if path.is_file() and not path.is_symlink()
    }
    expected_before_result = RAW_WORKSPACE_FILES - {"probe_result.json"}
    passed = (
        result.returncode == 0
        and all(report.values())
        and roundtrip_sha == challenge_sha
        and before_records == after_records
        and before_hash == after_hash
        and not adjacent.exists()
        and workspace_files_before_result == expected_before_result
    )
    payload: dict[str, Any] = {
        "schema_version": RAW_PROBE_SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "test_mode": False,
        "accepted_compute_preflight_evidence": passed,
        "probe_passed": passed,
        "scheduler_submission_performed_by_tool": False,
        "scheduler_contact_performed_by_tool": False,
        "apptainer_invoked": True,
        "geant4_invoked": False,
        "execution_id": execution.execution_id,
        "execution_hash": execution.execution_hash,
        "execution_schema_version": SUCCESSOR_EXECUTION_SCHEMA_VERSION,
        "execution_directory": str(execution_dir),
        "slurm_job_id": job_id,
        "slurm_job_name": job_name,
        "probe_token": token,
        "probe_workspace": str(workspace),
        "container_probe_root": container_probe_root,
        "container_adjacent_root": container_adjacent,
        "container_original_execution_path": str(execution_dir),
        "container_contract": {
            "cleanenv": True,
            "containall": True,
            "no_home": True,
            "no_mount": NO_MOUNT_CLASSES,
            "fixed_ro_mount_count": 5,
            "external_rw_mount_count": 1,
            "execution_root_read_only": True,
            "probe_root_read_write": True,
            "mountinfo_contract_version": "linux-proc-mountinfo-v1",
            "command_sha256": _canonical_hash(command),
        },
        "runtime": {
            "image_sha256": image_sha,
            "g4_data_manifest_sha256": data_manifest_sha,
            "executable_sha256": executable_sha,
            **engine_identity,
        },
        "compute_node": {
            "hostname": socket.gethostname(),
            "slurmd_nodename": environment_source.get("SLURMD_NODENAME", ""),
        },
        "portable_lock_diagnostics": lock_diagnostics,
        "host_environment": {
            "forbidden_variable_count": 0,
            "sanitized_environment_used": True,
        },
        "container_report": report,
        "return_code": result.returncode,
        "challenge_sha256": challenge_sha,
        "roundtrip_sha256": roundtrip_sha,
        "stdout_sha256": sha256_file(workspace / "stdout.txt"),
        "stderr_sha256": sha256_file(workspace / "stderr.txt"),
        "execution_snapshot_before_sha256": before_hash,
        "execution_snapshot_after_sha256": after_hash,
        "execution_snapshot_record_count": len(before_records),
        "execution_snapshot_unchanged": before_records == after_records,
        "adjacent_host_sibling_absent": not adjacent.exists(),
    }
    payload["raw_result_hash"] = _canonical_hash(payload)
    _write_exclusive_json(workspace / "probe_result.json", payload)
    if not passed:
        raise ValueError(f"R3 container isolation probe failed; evidence: {workspace}")
    return workspace


def validate_raw_probe_workspace(
    workspace: Path,
    *,
    allow_test_mode: bool = False,
    require_recorded_location: bool = True,
) -> dict[str, Any]:
    requested = workspace.expanduser()
    if requested.is_symlink() or not requested.is_dir():
        raise ValueError("raw R3 probe workspace must be a regular directory")
    directory = requested.resolve()
    entries = {path.name for path in directory.iterdir()}
    if entries != RAW_WORKSPACE_FILES or any(path.is_symlink() for path in directory.iterdir()):
        raise ValueError("raw R3 probe workspace file set mismatch")
    payload = load_json(directory / "probe_result.json")
    required = {
        "schema_version",
        "created_at_utc",
        "test_mode",
        "accepted_compute_preflight_evidence",
        "probe_passed",
        "scheduler_submission_performed_by_tool",
        "scheduler_contact_performed_by_tool",
        "apptainer_invoked",
        "geant4_invoked",
        "execution_id",
        "execution_hash",
        "execution_schema_version",
        "execution_directory",
        "slurm_job_id",
        "slurm_job_name",
        "probe_token",
        "probe_workspace",
        "container_probe_root",
        "container_adjacent_root",
        "container_original_execution_path",
        "container_contract",
        "runtime",
        "compute_node",
        "portable_lock_diagnostics",
        "host_environment",
        "container_report",
        "return_code",
        "challenge_sha256",
        "roundtrip_sha256",
        "stdout_sha256",
        "stderr_sha256",
        "execution_snapshot_before_sha256",
        "execution_snapshot_after_sha256",
        "execution_snapshot_record_count",
        "execution_snapshot_unchanged",
        "adjacent_host_sibling_absent",
        "raw_result_hash",
    }
    if set(payload) != required:
        raise ValueError("raw R3 probe result key set mismatch")
    recorded_hash = payload.pop("raw_result_hash", None)
    actual_hash = _canonical_hash(payload)
    payload["raw_result_hash"] = recorded_hash
    report = payload.get("container_report")
    contract = payload.get("container_contract")
    runtime = payload.get("runtime")
    compute_node = payload.get("compute_node")
    lock_diagnostics = payload.get("portable_lock_diagnostics")
    host_environment = payload.get("host_environment")
    test_mode = payload.get("test_mode")
    if (
        payload.get("schema_version") != RAW_PROBE_SCHEMA_VERSION
        or not isinstance(test_mode, bool)
        or (test_mode and not allow_test_mode)
        or payload.get("accepted_compute_preflight_evidence")
        is not (not test_mode)
        or payload.get("probe_passed") is not True
        or payload.get("scheduler_submission_performed_by_tool") is not False
        or payload.get("scheduler_contact_performed_by_tool") is not False
        or payload.get("apptainer_invoked") is not True
        or payload.get("geant4_invoked") is not False
        or payload.get("return_code") != 0
        or payload.get("execution_snapshot_unchanged") is not True
        or payload.get("adjacent_host_sibling_absent") is not True
        or recorded_hash != actual_hash
        or not isinstance(report, dict)
        or set(report) != set(REPORT_KEYS)
        or any(value is not True for value in report.values())
        or not isinstance(contract, dict)
        or set(contract)
        != {
            "cleanenv",
            "containall",
            "no_home",
            "no_mount",
            "fixed_ro_mount_count",
            "external_rw_mount_count",
            "execution_root_read_only",
            "probe_root_read_write",
            "mountinfo_contract_version",
            "command_sha256",
        }
        or contract.get("cleanenv") is not True
        or contract.get("containall") is not True
        or contract.get("no_home") is not True
        or contract.get("no_mount") != NO_MOUNT_CLASSES
        or contract.get("fixed_ro_mount_count") != 5
        or contract.get("external_rw_mount_count") != 1
        or contract.get("execution_root_read_only") is not True
        or contract.get("probe_root_read_write") is not True
        or contract.get("mountinfo_contract_version")
        != "linux-proc-mountinfo-v1"
        or not _is_sha256(contract.get("command_sha256"))
        or not isinstance(runtime, dict)
        or set(runtime)
        != {
            "image_sha256",
            "g4_data_manifest_sha256",
            "executable_sha256",
            "apptainer_path",
            "apptainer_sha256",
            "apptainer_version",
        }
        or any(
            not _is_sha256(runtime.get(name))
            for name in (
                "image_sha256",
                "g4_data_manifest_sha256",
                "executable_sha256",
            )
        )
        or not isinstance(runtime.get("apptainer_path"), str)
        or not _is_sha256(runtime.get("apptainer_sha256"))
        or not isinstance(runtime.get("apptainer_version"), str)
        or not runtime.get("apptainer_version")
        or not isinstance(compute_node, dict)
        or set(compute_node) != {"hostname", "slurmd_nodename"}
        or not isinstance(compute_node.get("hostname"), str)
        or not compute_node.get("hostname")
        or not isinstance(compute_node.get("slurmd_nodename"), str)
        or (not test_mode and not compute_node.get("slurmd_nodename"))
        or not isinstance(lock_diagnostics, dict)
        or set(lock_diagnostics)
        != {
            "protocol",
            "creation_device",
            "creation_inode",
            "compute_device",
            "compute_inode",
            "mode",
            "size_bytes",
            "sha256",
            "link_count",
            "cross_node_numeric_identity_required",
            "portable_identity_matches",
        }
        or lock_diagnostics.get("protocol") != "portable-flock-v2"
        or any(
            not isinstance(lock_diagnostics.get(name), int)
            or isinstance(lock_diagnostics.get(name), bool)
            or lock_diagnostics[name] < 0
            for name in (
                "creation_device",
                "creation_inode",
                "compute_device",
                "compute_inode",
                "size_bytes",
            )
        )
        or lock_diagnostics.get("mode") != 0o400
        or lock_diagnostics.get("link_count") != 1
        or not _is_sha256(lock_diagnostics.get("sha256"))
        or lock_diagnostics.get("cross_node_numeric_identity_required") is not False
        or lock_diagnostics.get("portable_identity_matches") is not True
        or host_environment
        != {
            "forbidden_variable_count": 0,
            "sanitized_environment_used": True,
        }
        or payload.get("execution_schema_version")
        != SUCCESSOR_EXECUTION_SCHEMA_VERSION
        or not _is_sha256(payload.get("execution_hash"))
        or not _is_sha256(recorded_hash)
        or not TOKEN_RE.fullmatch(str(payload.get("probe_token", "")))
        or payload.get("container_probe_root")
        != CONTAINER_PROBE_PREFIX + str(payload.get("probe_token"))
        or payload.get("container_adjacent_root")
        != CONTAINER_PROBE_PREFIX + "adjacent-" + str(payload.get("probe_token"))
        or payload.get("container_original_execution_path")
        != payload.get("execution_directory")
        or payload.get("execution_snapshot_before_sha256")
        != payload.get("execution_snapshot_after_sha256")
        or not _is_sha256(payload.get("execution_snapshot_before_sha256"))
        or not isinstance(payload.get("execution_snapshot_record_count"), int)
        or payload.get("execution_snapshot_record_count") < 1
        or payload.get("slurm_job_name")
        != expected_r3_job_name(str(payload.get("execution_hash")))
        or payload.get("challenge_sha256") != payload.get("roundtrip_sha256")
        or payload.get("challenge_sha256") != sha256_file(directory / "challenge.bin")
        or payload.get("roundtrip_sha256")
        != sha256_file(directory / "container-roundtrip.bin")
        or payload.get("stdout_sha256") != sha256_file(directory / "stdout.txt")
        or payload.get("stderr_sha256") != sha256_file(directory / "stderr.txt")
        or (
            require_recorded_location
            and payload.get("probe_workspace") != str(directory)
        )
        or not JOB_ID_RE.fullmatch(str(payload.get("slurm_job_id", "")))
    ):
        raise ValueError("raw R3 probe result semantic validation failed")
    parsed_report = _parse_container_report(directory / "container-report.tsv")
    if parsed_report != report:
        raise ValueError("raw R3 container report disagrees with result")
    return payload


def _parse_accounting_rows(text: str) -> list[dict[str, str]]:
    names = ("job_id", "job_name", "account", "state", "exit_code", "elapsed_raw")
    rows: list[dict[str, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        values = raw.split("|")
        if len(values) != len(names):
            raise ValueError(f"invalid R3 sacct row {number}: {raw!r}")
        rows.append(dict(zip(names, values)))
    return rows


def _held_scheduler_identity(
    text: str,
    *,
    job_id: str,
    job_name: str,
    formal_execution_dir: Path | None,
) -> dict[str, Any]:
    """Validate one externally captured held-job ``scontrol -o`` snapshot."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("R3 held-job evidence must contain one scontrol row")
    fields: dict[str, str] = {}
    for token in lines[0].split():
        if "=" not in token:
            continue
        name, value = token.split("=", 1)
        if name in fields:
            raise ValueError("R3 held-job evidence contains duplicate fields")
        fields[name] = value
    required = {
        "JobId",
        "JobName",
        "Account",
        "JobState",
        "Reason",
        "Requeue",
        "Command",
        "WorkDir",
        "StdOut",
    }
    if (
        not required <= set(fields)
        or fields["JobId"] != job_id
        or fields["JobName"] != job_name
        or fields["Account"].casefold() != FORMAL_ACCOUNT.casefold()
        or fields["JobState"] != "PENDING"
        or fields["Reason"] != "JobHeldUser"
        or fields["Requeue"] != "0"
        or not fields["Command"].startswith("/")
        or not fields["WorkDir"].startswith("/")
        or not fields["StdOut"].startswith("/")
        or fields.get("ArrayJobId") not in {None, "", "N/A"}
        or fields.get("ArrayTaskId") not in {None, "", "N/A"}
    ):
        raise ValueError("R3 held-job scheduler identity is not accepted")
    if formal_execution_dir is not None:
        execution = formal_execution_dir.expanduser().resolve()
        expected = {
            "Command": str(
                execution
                / "sources/control/hpc/osc/"
                "run_steel_module_production_r3_probe.sbatch"
            ),
            "WorkDir": str(execution),
            "StdOut": str(
                canonical_r3_evidence_root(execution) / f"slurm-{job_id}.out"
            ),
        }
        if any(fields.get(name) != value for name, value in expected.items()):
            raise ValueError("R3 held-job command/workdir/output identity mismatch")
    return {
        "job_id": job_id,
        "job_name": job_name,
        "account": fields["Account"],
        "state": fields["JobState"],
        "reason": fields["Reason"],
        "requeue": fields["Requeue"],
        "command": fields["Command"],
        "work_dir": fields["WorkDir"],
        "stdout": fields["StdOut"],
        "non_array": True,
        "scontrol_sha256": sha256_bytes(text.encode("utf-8")),
    }


def write_terminal_accounting_input(
    *,
    output_dir: Path,
    job_id: str,
    job_name: str,
    sacct_text: str,
    squeue_text: str,
    held_scontrol_text: str,
    formal_execution_dir: Path | None = None,
) -> Path:
    """Freeze externally collected terminal scheduler text without querying Slurm."""

    if not JOB_ID_RE.fullmatch(job_id) or not job_name or squeue_text.strip():
        raise ValueError("R3 accounting identity is invalid or job remains active")
    held_scheduler = _held_scheduler_identity(
        held_scontrol_text,
        job_id=job_id,
        job_name=job_name,
        formal_execution_dir=formal_execution_dir,
    )
    rows = _parse_accounting_rows(sacct_text)
    parents = [row for row in rows if row["job_id"] == job_id]
    if len(parents) != 1:
        raise ValueError("R3 accounting requires exactly one parent job row")
    parent = parents[0]
    identifiers = [row["job_id"] for row in rows]
    if (
        parent["job_name"] != job_name
        or parent["account"].casefold() != FORMAL_ACCOUNT.casefold()
        or parent["state"] != "COMPLETED"
        or parent["exit_code"] != "0:0"
        or not parent["elapsed_raw"].isdigit()
        or len(set(identifiers)) != len(identifiers)
        or any(
            row["job_id"] != job_id
            and not row["job_id"].startswith(job_id + ".")
            for row in rows
        )
        or any(
            row["account"].casefold() != FORMAL_ACCOUNT.casefold()
            or row["state"] != "COMPLETED"
            or row["exit_code"] != "0:0"
            or not row["elapsed_raw"].isdigit()
            for row in rows
        )
    ):
        raise ValueError("R3 terminal parent accounting is not accepted")
    requested = output_dir.expanduser()
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ValueError("R3 accounting target must not be a symlink")
    target = requested.parent.resolve() / requested.name
    if formal_execution_dir is not None:
        expected_parent = canonical_r3_evidence_root(formal_execution_dir) / "accounting"
        if target.parent != expected_parent:
            raise ValueError("formal R3 accounting target is outside its canonical root")
    if target.exists() or not target.parent.is_dir():
        raise ValueError("R3 accounting target exists or parent is missing")
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent))
    try:
        _write_exclusive_text(temporary / "sacct.psv", sacct_text)
        _write_exclusive_text(temporary / "squeue.psv", squeue_text)
        _write_exclusive_text(temporary / "held_scontrol.txt", held_scontrol_text)
        payload = {
            "schema_version": ACCOUNTING_INPUT_SCHEMA_VERSION,
            "created_at_utc": utc_now(),
            "scheduler_submission_performed_by_tool": False,
            "scheduler_query_performed_by_tool": False,
            "accepted_terminal": True,
            "job_id": job_id,
            "job_name": job_name,
            "account": FORMAL_ACCOUNT,
            "state": parent["state"],
            "exit_code": parent["exit_code"],
            "elapsed_raw": int(parent["elapsed_raw"]),
            "sacct_field_order": [
                "JobID",
                "JobName",
                "Account",
                "State",
                "ExitCode",
                "ElapsedRaw",
            ],
            "sacct_sha256": sha256_file(temporary / "sacct.psv"),
            "squeue_sha256": sha256_file(temporary / "squeue.psv"),
            "held_scheduler": held_scheduler,
        }
        payload["accounting_hash"] = _canonical_hash(payload)
        _write_exclusive_json(temporary / "terminal_accounting.json", payload)
        write_recursive_checksums(temporary)
        validate_terminal_accounting_input(temporary)
        publish_directory_no_replace(temporary, target)
        temporary = None
        validate_terminal_accounting_input(target)
        return target
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)


def validate_terminal_accounting_input(directory: Path) -> dict[str, Any]:
    records = verify_recursive_checksums(
        directory,
        required={
            "held_scontrol.txt",
            "sacct.psv",
            "squeue.psv",
            "terminal_accounting.json",
        },
    )
    if set(records) != {
        "held_scontrol.txt",
        "sacct.psv",
        "squeue.psv",
        "terminal_accounting.json",
    }:
        raise ValueError("R3 accounting input file set mismatch")
    root = directory.expanduser().resolve()
    payload = load_json(root / "terminal_accounting.json")
    required = {
        "schema_version",
        "created_at_utc",
        "scheduler_submission_performed_by_tool",
        "scheduler_query_performed_by_tool",
        "accepted_terminal",
        "job_id",
        "job_name",
        "account",
        "state",
        "exit_code",
        "elapsed_raw",
        "sacct_field_order",
        "sacct_sha256",
        "squeue_sha256",
        "held_scheduler",
        "accounting_hash",
    }
    if set(payload) != required:
        raise ValueError("R3 accounting payload key set mismatch")
    recorded_hash = payload.pop("accounting_hash", None)
    actual_hash = _canonical_hash(payload)
    payload["accounting_hash"] = recorded_hash
    sacct = (root / "sacct.psv").read_text(encoding="utf-8")
    squeue = (root / "squeue.psv").read_text(encoding="utf-8")
    held_scontrol = (root / "held_scontrol.txt").read_text(encoding="utf-8")
    held_scheduler = _held_scheduler_identity(
        held_scontrol,
        job_id=str(payload.get("job_id", "")),
        job_name=str(payload.get("job_name", "")),
        formal_execution_dir=None,
    )
    rows = _parse_accounting_rows(sacct)
    parents = [row for row in rows if row["job_id"] == payload.get("job_id")]
    identifiers = [row["job_id"] for row in rows]
    if (
        payload.get("schema_version") != ACCOUNTING_INPUT_SCHEMA_VERSION
        or payload.get("scheduler_submission_performed_by_tool") is not False
        or payload.get("scheduler_query_performed_by_tool") is not False
        or payload.get("accepted_terminal") is not True
        or payload.get("account") != FORMAL_ACCOUNT
        or payload.get("state") != "COMPLETED"
        or payload.get("exit_code") != "0:0"
        or not isinstance(payload.get("elapsed_raw"), int)
        or payload.get("elapsed_raw") < 0
        or recorded_hash != actual_hash
        or payload.get("sacct_field_order")
        != [
            "JobID",
            "JobName",
            "Account",
            "State",
            "ExitCode",
            "ElapsedRaw",
        ]
        or payload.get("sacct_sha256") != sha256_file(root / "sacct.psv")
        or payload.get("squeue_sha256") != sha256_file(root / "squeue.psv")
        or payload.get("held_scheduler") != held_scheduler
        or squeue.strip()
        or len(parents) != 1
        or len(set(identifiers)) != len(identifiers)
        or any(
            row["job_id"] != payload.get("job_id")
            and not row["job_id"].startswith(str(payload.get("job_id")) + ".")
            for row in rows
        )
        or any(
            row["account"].casefold() != FORMAL_ACCOUNT.casefold()
            or row["state"] != "COMPLETED"
            or row["exit_code"] != "0:0"
            or not row["elapsed_raw"].isdigit()
            for row in rows
        )
        or parents[0]["job_name"] != payload.get("job_name")
        or parents[0]["account"].casefold() != FORMAL_ACCOUNT.casefold()
        or parents[0]["state"] != "COMPLETED"
        or parents[0]["exit_code"] != "0:0"
        or parents[0]["elapsed_raw"] != str(payload.get("elapsed_raw"))
    ):
        raise ValueError("R3 terminal accounting semantic validation failed")
    return payload


def seal_r3_probe_evidence(
    *,
    raw_workspace: Path,
    terminal_accounting_dir: Path,
    output_root: Path,
    execution_dir: Path,
    control_root: Path,
    test_mode: bool = False,
) -> Path:
    """Seal one passed probe and matching terminal accounting content-addressably."""

    raw = validate_raw_probe_workspace(
        raw_workspace, allow_test_mode=test_mode
    )
    accounting = validate_terminal_accounting_input(terminal_accounting_dir)
    execution = load_successor_execution(
        execution_dir,
        repo_root=None if test_mode else control_root,
        allow_test_mode=test_mode,
        require_readiness=False,
        verify_runtime=not test_mode,
        verify_live_predecessor=True,
    )
    validate_successor_r2_boundary(execution, repo_root=control_root)
    if (
        raw.get("slurm_job_id") != accounting.get("job_id")
        or raw.get("slurm_job_name") != accounting.get("job_name")
        or raw.get("test_mode") is not test_mode
        or raw.get("execution_id") != execution.execution_id
        or raw.get("execution_hash") != execution.execution_hash
        or raw.get("execution_directory") != str(execution.directory)
    ):
        raise ValueError("R3 probe execution/accounting identity mismatch")
    expected_runtime = {
        "image_sha256": execution.manifest["runtime"]["image_sha256"],
        "g4_data_manifest_sha256": execution.manifest["runtime"]
        ["g4_data_manifest_sha256"],
        "executable_sha256": execution.manifest["runtime"]["executable_sha256"],
    }
    if any(raw["runtime"].get(key) != value for key, value in expected_runtime.items()):
        raise ValueError("R3 probe runtime identity differs from successor")
    root_requested = output_root.expanduser()
    if root_requested.is_symlink() or not root_requested.is_dir():
        raise ValueError("R3 evidence output root must be an existing regular directory")
    root = root_requested.resolve()
    if not test_mode:
        canonical = canonical_r3_evidence_root(execution.directory)
        if (
            raw_workspace.resolve().parent != canonical / "raw"
            or terminal_accounting_dir.resolve().parent != canonical / "accounting"
            or root != canonical / "evidence"
        ):
            raise ValueError("formal R3 inputs/output are outside the canonical evidence root")
    identity = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "execution_id": raw["execution_id"],
        "execution_hash": raw["execution_hash"],
        "raw_result_hash": raw["raw_result_hash"],
        "accounting_hash": accounting["accounting_hash"],
        "job_id": accounting["job_id"],
        "container_contract": raw["container_contract"],
        "runtime": raw["runtime"],
    }
    evidence_hash = _canonical_hash(identity)
    evidence_id = "sm-v1-r3-container-probe-" + evidence_hash[:12]
    target = root / evidence_id
    if target.exists() or target.is_symlink():
        raise ValueError(f"refusing to overwrite R3 evidence: {target}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{evidence_id}.tmp-", dir=root))
    try:
        shutil.copytree(raw_workspace.resolve(), temporary / "raw")
        shutil.copytree(terminal_accounting_dir.resolve(), temporary / "accounting")
        payload = {
            **identity,
            "created_at_utc": utc_now(),
            "evidence_id": evidence_id,
            "evidence_hash": evidence_hash,
            "test_mode": test_mode,
            "accepted_compute_preflight_evidence": not test_mode,
            "scheduler_submission_performed_by_tool": False,
            "scheduler_query_performed_by_tool": False,
            "apptainer_invoked": True,
            "geant4_invoked": False,
            "probe_result_sha256": sha256_file(
                temporary / "raw" / "probe_result.json"
            ),
            "accounting_manifest_sha256": sha256_file(
                temporary / "accounting" / "SHA256SUMS"
            ),
        }
        _write_exclusive_json(temporary / "evidence.json", payload)
        write_recursive_checksums(temporary)
        validate_r3_probe_evidence(
            temporary,
            allow_test_mode=test_mode,
            expected_evidence_id=evidence_id,
        )
        publish_directory_no_replace(temporary, target)
        temporary = None
        validate_r3_probe_evidence(target, allow_test_mode=test_mode)
        return target
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)


def validate_r3_probe_evidence(
    directory: Path,
    *,
    allow_test_mode: bool = False,
    expected_evidence_id: str | None = None,
) -> dict[str, Any]:
    records = verify_recursive_checksums(
        directory,
        required={
            "evidence.json",
            "raw/probe_result.json",
            "accounting/terminal_accounting.json",
            "accounting/SHA256SUMS",
        },
    )
    expected_records = {
        "evidence.json",
        *(f"raw/{name}" for name in RAW_WORKSPACE_FILES),
        "accounting/SHA256SUMS",
        "accounting/held_scontrol.txt",
        "accounting/sacct.psv",
        "accounting/squeue.psv",
        "accounting/terminal_accounting.json",
    }
    if set(records) != expected_records:
        raise ValueError("R3 evidence recursive file set mismatch")
    root = directory.expanduser().resolve()
    payload = load_json(root / "evidence.json")
    expected_payload_keys = {
        "schema_version",
        "execution_id",
        "execution_hash",
        "raw_result_hash",
        "accounting_hash",
        "job_id",
        "container_contract",
        "runtime",
        "created_at_utc",
        "evidence_id",
        "evidence_hash",
        "test_mode",
        "accepted_compute_preflight_evidence",
        "scheduler_submission_performed_by_tool",
        "scheduler_query_performed_by_tool",
        "apptainer_invoked",
        "geant4_invoked",
        "probe_result_sha256",
        "accounting_manifest_sha256",
    }
    if set(payload) != expected_payload_keys:
        raise ValueError("R3 evidence payload key set mismatch")
    raw = validate_raw_probe_workspace(
        root / "raw",
        allow_test_mode=allow_test_mode,
        require_recorded_location=False,
    )
    accounting = validate_terminal_accounting_input(root / "accounting")
    identity = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "execution_id": raw["execution_id"],
        "execution_hash": raw["execution_hash"],
        "raw_result_hash": raw["raw_result_hash"],
        "accounting_hash": accounting["accounting_hash"],
        "job_id": accounting["job_id"],
        "container_contract": raw["container_contract"],
        "runtime": raw["runtime"],
    }
    evidence_hash = _canonical_hash(identity)
    evidence_id = "sm-v1-r3-container-probe-" + evidence_hash[:12]
    test_mode = payload.get("test_mode")
    if (
        payload.get("schema_version") != EVIDENCE_SCHEMA_VERSION
        or payload.get("evidence_id") != evidence_id
        or payload.get("evidence_hash") != evidence_hash
        or (expected_evidence_id is None and root.name != evidence_id)
        or (
            expected_evidence_id is not None
            and expected_evidence_id != evidence_id
        )
        or not isinstance(test_mode, bool)
        or (test_mode and not allow_test_mode)
        or test_mode != raw.get("test_mode")
        or payload.get("accepted_compute_preflight_evidence")
        is not (not test_mode)
        or payload.get("scheduler_submission_performed_by_tool") is not False
        or payload.get("scheduler_query_performed_by_tool") is not False
        or payload.get("apptainer_invoked") is not True
        or payload.get("geant4_invoked") is not False
        or payload.get("probe_result_sha256")
        != sha256_file(root / "raw" / "probe_result.json")
        or payload.get("accounting_manifest_sha256")
        != sha256_file(root / "accounting" / "SHA256SUMS")
        or raw["slurm_job_id"] != accounting["job_id"]
        or raw["slurm_job_name"] != accounting["job_name"]
    ):
        raise ValueError("R3 probe evidence semantic validation failed")
    for key, value in identity.items():
        if payload.get(key) != value:
            raise ValueError(f"R3 probe evidence identity mismatch: {key}")
    return payload


__all__ = [
    "ACCOUNTING_INPUT_SCHEMA_VERSION",
    "CONTAINER_PROBE_PREFIX",
    "EVIDENCE_SCHEMA_VERSION",
    "RAW_PROBE_SCHEMA_VERSION",
    "REPORT_KEYS",
    "expected_r3_job_name",
    "run_r3_container_probe",
    "seal_r3_probe_evidence",
    "validate_r3_probe_evidence",
    "validate_raw_probe_workspace",
    "validate_terminal_accounting_input",
    "write_terminal_accounting_input",
]
