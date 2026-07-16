#!/usr/bin/env python3
"""Cheap deterministic checks for the steel-module campaign workflow."""

from __future__ import annotations

import csv
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import finalize_steel_module_campaign as finalizer
from audit_realistic_neutron_campaign import (
    SUMMARY_FLOAT_FIELDS,
    SUMMARY_INTEGER_FIELDS,
)
from realistic_neutron_event_audit import SIPM_SENSOR_FIELDS
from steel_module_campaign_lib import (
    ATTEMPT_FIELDS,
    CampaignTask,
    environment_identity,
    load_campaign,
    sha256_file,
    verify_finalized_checksums,
)


def run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if expect_success and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {shlex.join(args)}\n{result.stdout}"
        )
    if not expect_success and result.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {shlex.join(args)}")
    return result


def read_tasks(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def generate(
    repo_root: Path,
    output: Path,
    *,
    stage: str,
    extra: tuple[str, ...] = (),
) -> dict[str, object]:
    run(
        [
            sys.executable,
            "hpc/osc/generate_steel_module_campaign.py",
            "--out-dir",
            str(output),
            "--stage",
            stage,
            "--campaign-seed",
            "20260715",
            "--allow-dirty",
            *extra,
        ],
        cwd=repo_root,
    )
    return json.loads((output / "campaign.json").read_text(encoding="utf-8"))


def make_production_fixture(
    repo_root: Path,
    campaign_dir: Path,
    scratch: Path,
) -> tuple[dict[str, object], dict[Path, dict[str, int | float]]]:
    image = scratch / "geant4.sif"
    data_manifest = scratch / "g4-data-manifest.json"
    executable = scratch / "OpNovice2"
    image.write_bytes(b"fixture image\n")
    data_manifest.write_text('{"fixture": true}\n', encoding="utf-8")
    executable.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    manifest_path = campaign_dir / "campaign.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    manifest["git"] = {
        "commit": commit,
        "branch": "fixture",
        "dirty": False,
        "dirty_paths": [],
    }
    environment: dict[str, object] = {
        "mode": "osc-production",
        "geant4_version": "11.4.2",
        "accepted_statistical_evidence": True,
        "image": {"path": str(image.resolve()), "sha256": sha256_file(image)},
        "g4_data_manifest": {
            "path": str(data_manifest.resolve()),
            "sha256": sha256_file(data_manifest),
        },
        "build_artifact": {
            "path": str(executable.resolve()),
            "sha256": sha256_file(executable),
        },
    }
    environment["identity_hash"] = environment_identity(environment)
    manifest["environment"] = environment
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    attempt_id = "fixture-attempt"
    attempt_dir = campaign_dir / "attempts" / attempt_id
    attempt_dir.mkdir(parents=True)
    tasks = read_tasks(campaign_dir / "tasks.tsv")
    plan_lines = [
        line
        for line in (campaign_dir / "scan_args.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    ]
    root_reports: dict[Path, dict[str, int | float]] = {}
    mapped_rows: list[dict[str, object]] = []

    for array_index, (task, line) in enumerate(zip(tasks, plan_lines), start=1):
        logical_id = task["logical_task_id"]
        task_root = attempt_dir / "tasks" / logical_id
        env = os.environ.copy()
        env.update(
            {
                "SCAN_RUNS_DIR": str(task_root / "runs"),
                "LATEST_RUN_LINK": str(task_root / "latest"),
                "LATEST_POINTS_CSV": str(task_root / "points.csv"),
                "LATEST_RUN_CONFIG": str(task_root / "run_config.json"),
                "LATEST_EFFICIENCY_MAP": str(task_root / "efficiency_map.csv"),
                "SCAN_GIT_COMMIT": commit,
                "SCAN_GIT_BRANCH": "detached-fixture",
                "SCAN_GIT_DIRTY": "false",
                "G4RUN_MANAGER_TYPE": "Serial",
                "SCAN_RUN_ID_SUFFIX": f"{attempt_id}_{logical_id}",
                "RN_ATTEMPT_ID": attempt_id,
                "RN_LOGICAL_TASK_INDEX": task["task_index"],
                "RN_LOGICAL_TASK_ID": logical_id,
                "RN_PLAN_HASH": str(manifest["plan_hash"]),
                "RN_GIT_COMMIT": commit,
                "RN_ENVIRONMENT_MODE": "osc-production",
                "RN_ENVIRONMENT_IDENTITY": str(environment["identity_hash"]),
                "RN_IMAGE_SHA256": str(environment["image"]["sha256"]),
                "RN_G4_DATA_MANIFEST_SHA256": str(
                    environment["g4_data_manifest"]["sha256"]
                ),
                "RN_EXECUTABLE_SHA256": str(
                    environment["build_artifact"]["sha256"]
                ),
                "RN_TASK_RESULT_RECORDER": "record_steel_module_task_result.py",
                "SLURM_ARRAY_JOB_ID": "12345",
                "SLURM_ARRAY_TASK_ID": str(array_index),
                "SLURM_JOB_ID": str(12345 + array_index),
            }
        )
        run(
            ["./run_sipm_cavity_scan.sh", *shlex.split(line), "--dry-run"],
            cwd=repo_root / "test/OpNovice2",
            env=env,
        )
        config_path = next((task_root / "runs").glob("*/run_config.json"))
        run_dir = config_path.parent
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["execution"]["formal_campaign_task"] is True
        config["dry_run"] = False
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        with (run_dir / "points.csv").open(encoding="utf-8", newline="") as stream:
            point = next(csv.DictReader(stream))
        root_path = Path(point["root"])
        log_path = Path(point["log"])
        root_path.write_bytes(b"fixture ROOT payload\n")
        sensor_count = 4 if task["sipm_layout"] == "back-four" else 1
        log_path.write_text(
            "Checking overlaps for volume SteelAbsorber:0 (G4Box) ... OK!\n"
            + "".join(
                f"Checking overlaps for volume SiPM:{index} (G4Box) ... OK!\n"
                for index in range(sensor_count)
            ),
            encoding="utf-8",
        )

        report: dict[str, int | float] = {
            field: 0 for field in SUMMARY_INTEGER_FIELDS
        }
        report.update({field: 0.0 for field in SUMMARY_FLOAT_FIELDS})
        report.update(
            {
                "events": 1,
                "committed_events": 1,
                "shoot_position_events": 1,
                "primary_energy_events": 1,
                "generated_optical_photons": 10,
                "scintillation_photons": 10,
                "sipm_detected_photons": 2,
                "primary_neutron_interaction_events": 1,
                "primary_neutron_elastic_count": 1,
                "primary_neutron_elastic_events": 1,
                "charged_tile_entry_events": 1,
                "charged_tile_entry_count": 1,
                "steel_edep_sum_mev": 1.0,
                "tile_edep_sum_mev": 0.5,
            }
        )
        sensor_values = [2, 0, 0, 0]
        if task["sipm_layout"] == "back-four":
            sensor_values = [1, 1, 0, 0]
        for field, value in zip(SIPM_SENSOR_FIELDS, sensor_values):
            report[field] = value
        summary_path = root_path.with_name(f"{root_path.stem}_summary.csv")
        summary_fields = [*SUMMARY_INTEGER_FIELDS, *SUMMARY_FLOAT_FIELDS]
        with summary_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=summary_fields)
            writer.writeheader()
            writer.writerow({field: report[field] for field in summary_fields})
        (run_dir / "efficiency_map.csv").write_text(
            "tag,x,y,events\nfixture,0,0,1\n", encoding="utf-8"
        )
        root_reports[root_path.resolve()] = report

        run(
            [
                sys.executable,
                "hpc/osc/record_steel_module_task_result.py",
                "--campaign-dir",
                str(campaign_dir),
                "--attempt-id",
                attempt_id,
                "--logical-task-id",
                logical_id,
                "--task-root",
                str(task_root),
            ],
            cwd=repo_root,
            env=env,
        )
        mapped_rows.append({"array_index": array_index, **task})

    task_map = attempt_dir / "tasks.tsv"
    with task_map.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["array_index", *CampaignTask.__dataclass_fields__],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(mapped_rows)
    attempt_scan_args = attempt_dir / "scan_args.txt"
    attempt_scan_args.write_text("\n".join(plan_lines) + "\n", encoding="utf-8")
    (attempt_dir / "attempt.json").write_text(
        json.dumps(
            {
                "schema_version": "steel-module-submission-attempt-v1",
                "attempt_id": attempt_id,
                "campaign_id": manifest["campaign_id"],
                "plan_hash": manifest["plan_hash"],
                "mode": "submit",
                "status": "submitted",
                "task_count": len(tasks),
                "logical_task_ids": [task["logical_task_id"] for task in tasks],
                "frozen_source": "/fixture/frozen/source",
                "submitted_utc": "2026-07-15T00:00:00+00:00",
                "slurm_job_id": "12345",
                "submission_command": ["sbatch", "fixture"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    attempt_row = {
        "attempt_id": attempt_id,
        "submitted_utc": "2026-07-15T00:00:00+00:00",
        "mode": "submit",
        "status": "submitted",
        "campaign_id": manifest["campaign_id"],
        "plan_hash": manifest["plan_hash"],
        "git_commit": commit,
        "image_sha256": environment["image"]["sha256"],
        "g4_data_manifest_sha256": environment["g4_data_manifest"]["sha256"],
        "executable_sha256": environment["build_artifact"]["sha256"],
        "slurm_job_id": "12345",
        "array_spec": f"1-{len(tasks)}",
        "task_count": len(tasks),
        "attempt_tasks_tsv": str(task_map.relative_to(campaign_dir)),
        "scan_args_file": str(attempt_scan_args.relative_to(campaign_dir)),
        "frozen_source": "/fixture/frozen/source",
    }
    with (campaign_dir / "submission-attempts.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=ATTEMPT_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerow(attempt_row)
    return manifest, root_reports


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    run(
        [sys.executable, "hpc/osc/check_steel_module_scan_preset.py"],
        cwd=repo_root,
    )
    with tempfile.TemporaryDirectory(prefix="steel-module-campaign-check-") as raw:
        scratch = Path(raw)
        geometry = scratch / "geometry-smoke"
        generate(repo_root, geometry, stage="geometry-smoke")
        tasks = read_tasks(geometry / "tasks.tsv")
        assert len(tasks) == 18
        assert {int(task["tile_thickness_mm"]) for task in tasks} == {
            4,
            8,
            12,
            16,
            20,
            24,
        }
        assert {task["sipm_layout"] for task in tasks} == {
            "back-center",
            "edge-center",
            "back-four",
        }
        run(
            [
                sys.executable,
                "hpc/osc/submit_steel_module_campaign.py",
                "--campaign-dir",
                str(geometry),
                "--check-only",
            ],
            cwd=repo_root,
        )

        missing_events = scratch / "bad-benchmark"
        run(
            [
                sys.executable,
                "hpc/osc/generate_steel_module_campaign.py",
                "--out-dir",
                str(missing_events),
                "--stage",
                "benchmark",
                "--campaign-seed",
                "1",
                "--allow-dirty",
            ],
            cwd=repo_root,
            expect_success=False,
        )

        missing_pilot_selection = scratch / "bad-pilot"
        run(
            [
                sys.executable,
                "hpc/osc/generate_steel_module_campaign.py",
                "--out-dir",
                str(missing_pilot_selection),
                "--stage",
                "convergence-pilot",
                "--campaign-seed",
                "1",
                "--events",
                "1",
                "--blocks",
                "4",
                "--allow-dirty",
            ],
            cwd=repo_root,
            expect_success=False,
        )
        pilot_selection = scratch / "pilot-selection.tsv"
        pilot_selection.write_text(
            "tile_thickness_mm\tsipm_layout\tabsorber_transverse_mm\n"
            "4\tback-center\t300\n"
            "24\tback-four\t500\n",
            encoding="utf-8",
        )
        pilot = scratch / "pilot"
        generate(
            repo_root,
            pilot,
            stage="convergence-pilot",
            extra=(
                "--events",
                "1",
                "--blocks",
                "4",
                "--configurations-tsv",
                str(pilot_selection),
            ),
        )
        pilot_tasks = read_tasks(pilot / "tasks.tsv")
        assert len(pilot_tasks) == 8
        assert {
            (
                int(task["tile_thickness_mm"]),
                task["sipm_layout"],
                int(task["absorber_transverse_mm"]),
            )
            for task in pilot_tasks
        } == {(4, "back-center", 300), (24, "back-four", 500)}
        load_campaign(pilot, verify_external_artifacts=False)

        benchmark = scratch / "benchmark"
        generate(
            repo_root,
            benchmark,
            stage="benchmark",
            extra=("--events", "1"),
        )
        benchmark_tasks = read_tasks(benchmark / "tasks.tsv")
        assert len(benchmark_tasks) == 4
        assert {
            (int(task["tile_thickness_mm"]), task["sipm_layout"])
            for task in benchmark_tasks
        } == {
            (4, "back-center"),
            (24, "back-center"),
            (24, "edge-center"),
            (24, "back-four"),
        }

        _, root_reports = make_production_fixture(repo_root, benchmark, scratch)

        def fake_root_audit(
            path: Path, *, expected_events: int | None = None, context: str = ""
        ) -> tuple[dict[str, list[int]], dict[str, int | float]]:
            report = root_reports[path.resolve()]
            assert expected_events == report["events"]
            return {}, dict(report)

        finalizer.read_and_audit_root = fake_root_audit
        bundle = load_campaign(benchmark, verify_external_artifacts=True)
        attempts = finalizer.load_submitted_attempts(bundle)
        candidates, invalid = finalizer.collect_candidates(bundle, attempts)
        assert not invalid
        selected = finalizer.select_candidates(bundle, candidates, {})
        output = benchmark / "finalized"
        finalizer.finalize_outputs(
            bundle,
            output,
            selected,
            invalid,
            bootstrap_seed=20260715,
            bootstrap_resamples=100,
        )
        verify_finalized_checksums(output)
        validation = json.loads(
            (output / "validation_report.json").read_text(encoding="utf-8")
        )
        event_audit = json.loads(
            (output / "event_audit.json").read_text(encoding="utf-8")
        )
        assert validation["valid"] is True
        assert validation["event_audit_integrated"] is True
        assert validation["selected_tasks"] == 4
        assert event_audit["valid"] is True
        assert event_audit["task_count"] == 4
        with (output / "configuration_summary.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            configurations = list(csv.DictReader(stream))
        assert len(configurations) == 4
        back_four = next(
            row for row in configurations if row["sipm_layout"] == "back-four"
        )
        assert int(back_four["sipm_sensor_0_detected_photons"]) == 1
        assert int(back_four["sipm_sensor_1_detected_photons"]) == 1

    print(
        "steel-module campaign infrastructure: PASS "
        "(18-task smoke plan, 4-task benchmark fixture, integrated event audit)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
