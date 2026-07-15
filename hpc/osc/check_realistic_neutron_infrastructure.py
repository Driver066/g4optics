#!/usr/bin/env python3
"""Cheap deterministic checks for the realistic-neutron infrastructure."""

from __future__ import annotations

import csv
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


EXPECTED_SCAN_COLUMNS = [
    "event_id",
    "shoot_x_mm",
    "shoot_y_mm",
    "shoot_z_mm",
    "hit_valid",
    "hit_x_mm",
    "hit_y_mm",
    "hit_z_mm",
    "scint_centroid_valid",
    "scint_centroid_x_mm",
    "scint_centroid_y_mm",
    "scint_centroid_z_mm",
    "generated_optical_photons",
    "scintillation_photons",
    "sipm_detected_photons",
    "collection_efficiency",
    "primary_kinetic_energy_mev",
    "collection_efficiency_valid",
    "cerenkov_photons",
    "steel_edep_mev",
    "primary_neutron_elastic_count",
    "primary_neutron_inelastic_count",
    "primary_neutron_capture_count",
    "primary_neutron_elastic_flag",
    "primary_neutron_inelastic_flag",
    "primary_neutron_capture_flag",
    "charged_tile_entry_count",
    "charged_tile_entry_ke_mev",
    "electron_tile_entry_count",
    "electron_tile_entry_ke_mev",
    "proton_tile_entry_count",
    "proton_tile_entry_ke_mev",
    "other_charged_tile_entry_count",
    "other_charged_tile_entry_ke_mev",
    "primary_neutron_tile_entry_valid",
    "primary_neutron_tile_entry_x_mm",
    "primary_neutron_tile_entry_y_mm",
    "primary_neutron_tile_entry_z_mm",
    "tile_edep_mev",
    "electron_tile_edep_mev",
    "proton_tile_edep_mev",
    "other_charged_tile_edep_mev",
    "neutral_tile_edep_mev",
]


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


def generate_campaign(
    *,
    repo_root: Path,
    out_dir: Path,
    stage: str,
    extra: list[str] | None = None,
) -> dict[str, object]:
    args = [
        sys.executable,
        "hpc/osc/generate_realistic_neutron_campaign.py",
        "--out-dir",
        str(out_dir),
        "--stage",
        stage,
        "--campaign-seed",
        "20260714",
        "--allow-dirty",
    ]
    if extra:
        args.extend(extra)
    run(args, cwd=repo_root)
    return json.loads((out_dir / "campaign.json").read_text(encoding="utf-8"))


def validate_event_schema(opnovice_dir: Path) -> None:
    histo_source = (opnovice_dir / "src/HistoManager.cc").read_text(encoding="utf-8")
    start = histo_source.index('CreateNtuple("scan"')
    end = histo_source.index("FinishNtuple();", start)
    scan_booking = histo_source[start:end]
    columns = re.findall(r'CreateNtuple[IDS]Column\("([^"]+)"\)', scan_booking)
    assert columns == EXPECTED_SCAN_COLUMNS, (columns, EXPECTED_SCAN_COLUMNS)

    event_source = (opnovice_dir / "src/EventAction.cc").read_text(encoding="utf-8")
    filled_ids = [
        int(value)
        for value in re.findall(r"FillNtuple[IDS]Column\(\s*(\d+)", event_source)
    ]
    assert sorted(filled_ids) == list(range(len(EXPECTED_SCAN_COLUMNS))), filled_ids


def validate_runner_campaign(repo_root: Path, temp_root: Path) -> None:
    opnovice_dir = repo_root / "test/OpNovice2"
    campaign_dir = temp_root / "geometry-smoke"
    manifest = generate_campaign(
        repo_root=repo_root, out_dir=campaign_dir, stage="geometry-smoke"
    )
    assert manifest["task_count"] == 6

    repeat_dir = temp_root / "geometry-smoke-repeat"
    repeat_manifest = generate_campaign(
        repo_root=repo_root, out_dir=repeat_dir, stage="geometry-smoke"
    )
    assert repeat_manifest["campaign_id"] == manifest["campaign_id"]
    assert (repeat_dir / "tasks.tsv").read_bytes() == (campaign_dir / "tasks.tsv").read_bytes()

    plan_lines = [
        line
        for line in (campaign_dir / "scan_args.txt").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(plan_lines) == 6

    runner_root = temp_root / "runner"
    env = os.environ.copy()
    env.update(
        {
            "SCAN_RUNS_DIR": str(runner_root / "runs"),
            "LATEST_RUN_LINK": str(runner_root / "latest"),
            "LATEST_POINTS_CSV": str(runner_root / "points.csv"),
            "LATEST_RUN_CONFIG": str(runner_root / "run_config.json"),
            "LATEST_EFFICIENCY_MAP": str(runner_root / "efficiency_map.csv"),
        }
    )
    for line in plan_lines:
        args = ["./run_sipm_cavity_scan.sh", *shlex.split(line), "--dry-run"]
        run(args, cwd=opnovice_dir, env=env)

    configs = sorted((runner_root / "runs").glob("*/run_config.json"))
    assert len(configs) == 6
    expected_geometries = {
        (4, 200),
        (4, 300),
        (4, 500),
        (16, 200),
        (16, 300),
        (16, 500),
    }
    actual_geometries: set[tuple[int, int]] = set()
    seen_seeds: set[int] = set()
    for config_path in configs:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["schema_version"] == "opnovice2-run-config-v2"
        assert config["study_preset"] == "realistic-neutron-v1"
        assert config["simulation"]["primary_particle"] == "neutron"
        assert config["simulation"]["derived_gps_kinetic_energy_mev"] == 432.58
        assert config["beam"]["divergence_mrad"] == 55
        tile = int(config["tank"]["size"].split()[2])
        absorber = int(config["absorber"]["full_size_mm"][0])
        expected_source_z = 43.5 if tile == 4 else 49.5
        assert config["simulation"]["beam_z"] == f"{expected_source_z:g} mm"
        assert config["grid"]["point_count"] == 1
        assert config["campaign"]["configuration_hash"]
        seed1 = config["random"]["seed1"]
        seed2 = config["random"]["seed2"]
        assert seed1 != seed2
        assert seed1 not in seen_seeds and seed2 not in seen_seeds
        seen_seeds.update((seed1, seed2))
        actual_geometries.add((tile, absorber))

        macro_path = next(config_path.parent.glob("macros/*.mac"))
        macro = macro_path.read_text(encoding="utf-8")
        assert "/opnovice2/absorber/enabled true" in macro
        assert f"/opnovice2/absorber/size {absorber} {absorber} 40 mm" in macro
        assert "/gps/particle neutron" in macro
        assert "/gps/energy 432.58 MeV" in macro
        assert "/gps/ang/sigma_x 55 mrad" in macro
        assert f"/random/setSeeds {seed1} {seed2}" in macro
    assert actual_geometries == expected_geometries
    assert len(seen_seeds) == 12

    locked = run(
        [
            "./run_sipm_cavity_scan.sh",
            *shlex.split(plan_lines[0]),
            "--beam-z",
            "43.5",
            "--dry-run",
        ],
        cwd=opnovice_dir,
        env=env,
        expect_success=False,
    )
    assert "locks these options: --beam-z" in locked.stdout

    multi_point = run(
        [
            "./run_sipm_cavity_scan.sh",
            *shlex.split(plan_lines[0]),
            "--x-max",
            "5",
            "--dry-run",
        ],
        cwd=opnovice_dir,
        env=env,
        expect_success=False,
    )
    assert "requires exactly one scan point per invocation" in multi_point.stdout

    legacy_root = temp_root / "legacy-runner"
    legacy_env = env.copy()
    legacy_env.update(
        {
            "SCAN_RUNS_DIR": str(legacy_root / "runs"),
            "LATEST_RUN_LINK": str(legacy_root / "latest"),
            "LATEST_POINTS_CSV": str(legacy_root / "points.csv"),
            "LATEST_RUN_CONFIG": str(legacy_root / "run_config.json"),
            "LATEST_EFFICIENCY_MAP": str(legacy_root / "efficiency_map.csv"),
        }
    )
    run(
        [
            "./run_sipm_cavity_scan.sh",
            "full",
            "custom",
            "--events",
            "1",
            "--source-mode",
            "gps",
            "--tank-size",
            "50 50 4 mm",
            "--x-min",
            "0",
            "--x-max",
            "0",
            "--y-min",
            "0",
            "--y-max",
            "0",
            "--step",
            "1",
            "--grid-unit",
            "mm",
            "--dry-run",
        ],
        cwd=opnovice_dir,
        env=legacy_env,
    )
    legacy_config_path = next((legacy_root / "runs").glob("*/run_config.json"))
    legacy_config = json.loads(legacy_config_path.read_text(encoding="utf-8"))
    assert legacy_config["study_preset"] is None
    assert legacy_config["absorber"]["enabled"] is False
    legacy_macro_path = next(legacy_config_path.parent.glob("macros/*.mac"))
    legacy_macro = legacy_macro_path.read_text(encoding="utf-8")
    assert "/opnovice2/absorber/enabled" not in legacy_macro
    assert "/gps/particle e-" in legacy_macro


def validate_stage_shapes(repo_root: Path, temp_root: Path) -> None:
    cases = (
        ("benchmark", [], 2),
        ("convergence-pilot", [], 24),
        ("center-production", ["--events", "10", "--blocks", "4"], 8),
        ("line-scan", ["--events", "10", "--blocks", "4"], 72),
    )
    all_seeds: set[int] = set()
    for stage, extra, expected_count in cases:
        out_dir = temp_root / stage
        manifest = generate_campaign(
            repo_root=repo_root, out_dir=out_dir, stage=stage, extra=extra
        )
        tasks = read_tasks(out_dir / "tasks.tsv")
        assert manifest["task_count"] == expected_count
        assert len(tasks) == expected_count
        stage_seeds = {
            int(task[key]) for task in tasks for key in ("seed1", "seed2")
        }
        assert len(stage_seeds) == expected_count * 2
        all_seeds.update(stage_seeds)
        if stage == "line-scan":
            xs = {int(task["x_mm"]) for task in tasks}
            assert xs == {-20, 5, 10, 15, 20, 25, 30, 35, 40}
            assert 0 not in xs


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    opnovice_dir = repo_root / "test/OpNovice2"
    run(["bash", "-n", "run_sipm_cavity_scan.sh"], cwd=opnovice_dir)
    run(
        [sys.executable, "-m", "py_compile", "hpc/osc/generate_realistic_neutron_campaign.py"],
        cwd=repo_root,
    )
    validate_event_schema(opnovice_dir)
    with tempfile.TemporaryDirectory(prefix="g4optics-realistic-neutron-") as temp:
        temp_root = Path(temp)
        validate_runner_campaign(repo_root, temp_root)
        validate_stage_shapes(repo_root, temp_root)
    print("realistic-neutron infrastructure checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
