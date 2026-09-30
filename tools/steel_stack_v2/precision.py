#!/usr/bin/env python3
"""Prepare 8,000 calibration events; freeze independent main counts without submission."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import json
import io
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import numpy as np

import osc
from benchmark_audit import collect as audit_benchmark
from model import canonical_json, configuration_hash, prepare_tasks, validate_tasks
from precision_io import (CONTROLLER_FILES, IDENTITIES, PURPOSE, accepted_index, analysis_identity,
                          event_block, save_new)
from precision_math import SPEC, PlanningError, compare_pair, group_statistics


def prepare(args):
    batch = args.batch_dir.resolve()
    osc.require(not batch.exists(), "Use a new calibration batch; existing records are immutable")
    benchmark_bundle = args.benchmark_bundle.resolve()
    benchmark = osc.read(benchmark_bundle/"manifest.json")
    benchmark_report = osc.read(args.benchmark_audit)
    verified = audit_benchmark(benchmark_bundle, Path(benchmark["remote_output_root"]),
                              osc.sha256(benchmark_bundle/"manifest.json"))
    osc.require(canonical_json(verified) == canonical_json(benchmark_report), "Benchmark audit changed or failed")
    qualification = osc.read(args.qualification_report)
    registry = osc.read(args.qualification_report.parent/"registry.json")
    osc.require(qualification["passed"] is True and qualification["profile"] == "painted-corner-v2" and
                qualification["scale"] == 16 and qualification["architecture"] == "x86_64" and
                registry["build_receipt_sha256"] == osc.sha256(args.build_receipt), "Missing matching remote numerical qualification")
    osc.require(Path(sys.executable).resolve() == args.analysis_python.resolve(), "Prepare must use the declared analysis interpreter")
    previous = osc.read(benchmark_bundle/"tasks.json")
    excluded = sorted({t[k] for t in previous for k in ("seed1", "seed2")})
    plan = osc.validate_inputs(args.source_manifest, args.build_receipt, matrix="sensitivity", gaps=[.5,1.],
        events_per_task=100, blocks=10, campaign_seed=SPEC["calibration_seed"], total_event_budget=8000,
        optical_numerics="painted-corner-v2", corner_scale=16, purpose="calibration", excluded_seeds=excluded)
    osc.require(all(plan[k] == benchmark[k] for k in IDENTITIES), "Calibration changed the benchmark simulation identity")
    for protected in (Path(plan["remote_paths"]["source_root"]), benchmark_bundle, Path(benchmark["remote_output_root"])):
        osc.require(batch != protected and protected not in batch.parents, "Calibration would modify prior evidence")
    batch.mkdir(parents=True)
    controller = batch/"controller"
    controller.mkdir()
    source = Path(__file__).resolve().parent
    for name in CONTROLLER_FILES:
        shutil.copyfile(source/name, controller/name)
    save_new(controller/"controller.json", dict(schema_version="steel-precision-controller-v1",
        files={name:osc.sha256(controller/name) for name in CONTROLLER_FILES}, analysis_identity=analysis_identity()))
    save_new(batch/"statistics-spec.json", SPEC)
    save_new(batch/"excluded-benchmark-seeds.json", excluded)
    evidence = batch/"prior-evidence"
    evidence.mkdir()
    for name, path in (("simulation-source-manifest.json",args.source_manifest),
                       ("remote-build.json",args.build_receipt), ("benchmark-audit.json",args.benchmark_audit),
                       ("benchmark-manifest.json",benchmark_bundle/"manifest.json"),
                       ("benchmark-tasks.json",benchmark_bundle/"tasks.json"),
                       ("qualification-result.json",args.qualification_report),
                       ("qualification-registry.json",args.qualification_report.parent/"registry.json")):
        shutil.copyfile(path, evidence/name)
    save_new(evidence/"SHA256.json", {p.name:osc.sha256(p) for p in sorted(evidence.iterdir())})
    plan["calibration_control"] = dict(root=str(controller), sha256=osc.sha256(controller/"controller.json"),
        python=str(args.analysis_python.absolute()), stop_file=str(batch/"STOP.json"),
        spec_sha256=osc.sha256(batch/"statistics-spec.json"),
        excluded_seeds_sha256=osc.sha256(batch/"excluded-benchmark-seeds.json"),
        prior_evidence_sha256=osc.sha256(evidence/"SHA256.json"))
    plan["precision_spec"] = SPEC
    manifest = osc.render(plan, batch/"bundle", remote_bundle_root=str(batch/"bundle"),
        remote_output_root=str(batch/"results"), account=args.account, time_minutes=60, memory_gib=4,
        max_parallel=4, node_constraint="40core")
    manifest_hash = osc.sha256(batch/"bundle/manifest.json")
    command = [str(args.analysis_python.absolute()), str(controller/"precision.py"), "estimate",
               "--batch-dir", str(batch), "--manifest-sha256", manifest_hash, "--output-dir", str(batch/"analysis")]
    script = "\n".join(["#!/bin/bash", "#SBATCH --job-name=g4-gap-calibration-estimate",
        "#SBATCH --account="+args.account, "#SBATCH --nodes=1", "#SBATCH --ntasks=1", "#SBATCH --cpus-per-task=1",
        "#SBATCH --mem=4G", "#SBATCH --constraint=40core", "#SBATCH --time=00:15:00", "#SBATCH --no-requeue",
        "#SBATCH --output="+str(batch/"estimate-slurm-%j.log"), "set -euo pipefail", "module load python/3.12",
        "export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1",
        "exec "+shlex.join(command)])+"\n"
    (batch/"estimate.sbatch").write_text(script)
    save_new(batch/"preparation.json", dict(prepared_at_utc=datetime.now(timezone.utc).isoformat(),
        manifest_sha256=manifest_hash, controller_sha256=plan["calibration_control"]["sha256"],
        array_script_sha256=osc.sha256(batch/"bundle/array.sbatch"),
        estimate_script_sha256=osc.sha256(batch/"estimate.sbatch"),
        summary=manifest["summary"], purpose=PURPOSE, submitted=False, main_submitted=False))
    print(json.dumps(dict(batch=str(batch), manifest_sha256=manifest_hash, tasks=80, calibration_events=8000,
                          submitted=False, main_submitted=False), indent=2))


def csv_rows(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def serializable_group(group):
    return {k:v for k,v in group.items() if not isinstance(v, np.ndarray)}


def verify_scheduler(entries, output):
    jobs = sorted({entry["execution"]["scheduler_job"] for entry in entries})
    command = ["sacct", "-j", ",".join(jobs), "-X", "--parsable2",
               "--format=JobID,State,ExitCode,AllocCPUS,ElapsedRaw,NodeList"]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60)
    (output/"scheduler-accounting.txt").write_text(result.stdout)
    rows = {r["JobID"]:r for r in csv.DictReader(io.StringIO(result.stdout), delimiter="|")}
    for entry in entries:
        e = entry["execution"]
        key = e["scheduler_job"]+"_"+str(e["array_index"])
        osc.require(key in rows and rows[key]["State"] == "COMPLETED" and rows[key]["ExitCode"] == "0:0" and
                    rows[key]["AllocCPUS"] == "1", "Scheduler completion/allocation mismatch: "+key)
    return rows


def freeze_main(pairs, configurations, calibration_tasks, excluded, manifest, output, rates):
    """Proposal only: no scheduler script or submission capability is emitted."""
    used = set(excluded) | {t[k] for t in calibration_tasks for k in ("seed1", "seed2")}
    tasks = []
    allocation = []
    for pair in pairs:
        n = pair["conservative_events_per_arm"]
        configs = [configurations[(pair["tile_thickness_mm"], pair["layout"], g)] for g in (.5,1.)]
        registered = prepare_tasks(configs, events_per_task=100, blocks=n//100,
            campaign_seed=SPEC["main_seed"], total_event_budget=2*n, stage="science", excluded_seeds=used)
        for task in registered:
            task["purpose"] = "science"
            used.update((task["seed1"], task["seed2"]))
        tasks.extend(registered)
        for cfg in configs:
            rate = rates[configuration_hash(cfg)]
            allocation.append(dict(layout=cfg["layout"], tile_thickness_mm=cfg["tile_thickness_mm"],
                gap_mm=cfg["gap_mm"], events=n, tasks=n//100,
                measured_seconds_per_event=rate["mean"],
                estimated_core_hours=n*rate["mean"]/3600,
                conservative_core_hours=n*2*rate["worst"]/3600+(n//100)*300/3600,
                task_time_margin_passed=2*rate["worst"]*100+300 <= 3600))
    summary = validate_tasks(tasks, total_event_budget=sum(t["events"] for t in tasks))
    # An inadequate observed one-hour margin must not silently alter task size.
    ready = all(row["task_time_margin_passed"] for row in allocation)
    proposal = dict(schema_version="steel-gap-main-proposal-v1", state="frozen-proposal-not-submitted",
        purpose="independent-main-sample-proposal", submitted=False, executable=ready,
        reason=None if ready else "Calibration timing exceeded the registered one-hour resource margin",
        scientific_approval_required=True, statistical_precision_guaranteed=False,
        main_precision_verified=False, calibration_included=False, benchmark_included=False,
        summary=summary, resources=SPEC["resources"], simulation_identity={k:manifest[k] for k in IDENTITIES},
        calibration_manifest_sha256=osc.sha256(Path(manifest["remote_bundle_root"])/"manifest.json"),
        statistics_spec_sha256=manifest["calibration_control"]["spec_sha256"], allocation=allocation,
        estimated_core_hours=sum(x["estimated_core_hours"] for x in allocation),
        conservative_core_hours=sum(x["conservative_core_hours"] for x in allocation))
    target = output/"formal-plan"
    target.mkdir()
    save_new(target/"tasks.json", tasks)
    proposal["tasks_sha256"] = osc.sha256(target/"tasks.json")
    save_new(target/"manifest.json", proposal)
    csv_rows(target/"allocation.csv", allocation)
    return proposal


def plots(groups, output, *, sample_label="Independent calibration"):
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["MPLCONFIGDIR"] = str(output/"plot-cache")
    import matplotlib.pyplot as plt
    keys = sorted(groups)
    fig, axes = plt.subplots(4, 2, figsize=(11, 12), constrained_layout=True)
    for ax, key in zip(axes.flat, keys):
        x = groups[key]["response"]
        ax.hist(np.log10(1+x), bins=30, color="#3975a3", edgecolor="white")
        ax.set(title=f"{key[1]}, t={key[0]:g} mm, g={key[2]:g} mm",
               xlabel="log10(1 + collected photons / neutron)", ylabel="Neutron events")
    fig.suptitle(sample_label+" only — includes zero responses", fontsize=14)
    for suffix in ("png", "pdf"):
        fig.savefig(output/("event-distributions."+suffix), dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 7), constrained_layout=True)
    for key in keys:
        x = np.sort(groups[key]["response"])
        if x.sum() > 0:
            ax.plot(np.arange(1, len(x)+1)/len(x), np.cumsum(x)/x.sum(),
                    label=f"{key[1]}, t={key[0]:g}, g={key[2]:g}")
    ax.plot([0,1], [0,1], color="gray", linestyle=":")
    ax.set(xlabel="Fraction of neutron events (smallest response first)",
           ylabel="Fraction of collected photons", title=sample_label+" tail contributions — no event trimming")
    ax.legend(fontsize=8)
    for suffix in ("png", "pdf"):
        fig.savefig(output/("tail-contributions."+suffix), dpi=160)
    plt.close(fig)


def estimate(args):
    batch = args.batch_dir.resolve()
    output = args.output_dir.resolve()
    osc.require(not output.exists(), "Preserve the previous estimate; select a new output directory")
    for protected in (batch/"controller", batch/"bundle", batch/"results", batch/"prior-evidence"):
        osc.require(output != protected and protected not in output.parents, "Estimate would modify frozen inputs/results")
    output.mkdir(parents=True)
    try:
        manifest, entries = accepted_index(batch/"bundle", args.manifest_sha256)
        verify_scheduler(entries, output)
        save_new(output/"accepted-osc-index.json", dict(schema_version="steel-calibration-accepted-index-v1",
            manifest_sha256=args.manifest_sha256, purpose=PURPOSE, entries=entries))
        grouped = defaultdict(list)
        configurations = {}
        rates = defaultdict(list)
        for entry in entries:
            block = event_block(entry)
            cfg = block["config"]
            key = (cfg["tile_thickness_mm"], cfg["layout"], cfg["gap_mm"])
            grouped[key].append(block)
            configurations[key] = cfg
            e = entry["execution"]
            rates[configuration_hash(cfg)].append(e["launcher_elapsed_seconds"]/entry["task"]["events"])
        groups = {}
        diagnostics, block_rows = [], []
        for key, blocks in sorted(grouped.items()):
            osc.require(len(blocks) == 10 and sum(len(b["values"]) for b in blocks) == 1000, "Wrong calibration group size")
            fields = blocks[0]["fields"]
            osc.require(all(b["fields"] == fields for b in blocks), "Event columns changed within configuration")
            result = group_statistics(blocks, fields)
            groups[key] = result
            ident = dict(tile_thickness_mm=key[0], layout=key[1], gap_mm=key[2])
            diagnostics.append(dict(ident, **result["statistics"]))
            block_rows.extend(dict(ident, **b) for b in result["blocks"])
            label = f"t{int(key[0]):02d}-{key[1]}-g{key[2]:g}"
            save_new(output/(label+"-calibration.json"), dict(ident, **serializable_group(result)))
            np.savez_compressed(output/(label+"-bootstrap.npz"), mean=result["bootstrap_mean"],
                variance=result["bootstrap_variance"], sums=result["bootstrap_sums"], fields=np.asarray(fields))
        csv_rows(output/"configuration-diagnostics.csv", diagnostics)
        csv_rows(output/"independent-blocks.csv", block_rows)
        plots(groups, output)
        pairs = []
        for t, layout in SPEC["pairs"]:
            pair = compare_pair(groups[(t,layout,.5)], groups[(t,layout,1.)], thickness=t, layout=layout)
            np.savez_compressed(output/(f"t{t:02d}-{layout}-pair-bootstrap.npz"),
                                delta=pair.pop("bootstrap_delta"), A=pair.pop("bootstrap_A"))
            pairs.append(pair)
        save_new(output/"four-comparisons.json", pairs)
        table = [dict(tile_thickness_mm=p["tile_thickness_mm"], layout=p["layout"],
            calibration_relative_change=p["calibration_delta"],
            ci95_low=p["pointwise95"]["low"], ci95_high=p["pointwise95"]["high"],
            ci9875_low=p["corrected9875"]["low"], ci9875_high=p["corrected9875"]["high"],
            calibration_corrected_half_width=p["corrected9875"]["half_width"],
            A_hat=p["A_hat"], A_bootstrap_p90=p["A_bootstrap_p90"], A_plus=p["A_plus"],
            point_events_per_arm=p["point_events_per_arm"],
            conservative_events_per_arm=p["conservative_events_per_arm"],
            first_half_point_events=p["half_sample_checks"][0].get("point_events_per_arm"),
            second_half_point_events=p["half_sample_checks"][1].get("point_events_per_arm"),
            calibration_only=True, main_precision_verified=False) for p in pairs]
        csv_rows(output/"sample-size-table.csv", table)
        excluded = osc.read(batch/"excluded-benchmark-seeds.json")
        rates = {k:dict(mean=float(np.mean(v)), worst=max(v)) for k,v in rates.items()}
        proposal = freeze_main(pairs, configurations, [e["task"] for e in entries], excluded, manifest, output, rates)
        result = dict(passed=True, calibration_events=8000, calibration_configurations=8, tasks=80,
            statistical_design_valid=True, main_submitted=False, main_precision_verified=False,
            main_executable_proposal=proposal["executable"], main_event_proposal=proposal["summary"]["events"],
            main_task_proposal=proposal["summary"]["tasks"], estimated_main_core_hours=proposal["estimated_core_hours"],
            target_half_width=.05, nominal_family_confidence=.95, calibration_in_main=False,
            benchmark_in_main=False, equivalence_tested=False, gap_selected=None,
            manifest_sha256=args.manifest_sha256, statistics_spec=SPEC,
            numerical_corrections=sum(e["numerical"]["corrections"] for e in entries),
            no_rindex=sum(e["numerical"]["boundary_no_rindex"] for e in entries),
            actual_calibration_launcher_hours=sum(e["execution"]["launcher_elapsed_seconds"] for e in entries)/3600)
        lines = ["# 八配置间隙评估：独立校准与正式样本量建议", "",
            "8 配置各 1,000 个完整中子事件通过审计；本报告仅用于规划独立正式样本。",
            "benchmark 与本批校准事件均不并入未来正式比较。没有提交正式扫描，也没有选择间隙。", "",
            "主指标为全模块收光数／入射中子，保留零响应。四组比较始终以 0.5 mm 为参照。",
            "完整事件在独立任务块内重采 10,000 次，分析种子 2026093002；辅助指标共用事件权重。",
            "校准区间同时列出逐项 95% 和 Bonferroni 校正 98.75%，后者对应名义整体 95%。", "",
            "|厚度 mm|布局|点估计事件/间隙|保守事件/间隙|前500估计|后500估计|",
            "|---:|---|---:|---:|---:|---:|"]
        for p in table:
            lines.append(f"|{p['tile_thickness_mm']}|{p['layout']}|{p['point_events_per_arm']}|{p['conservative_events_per_arm']}|{p['first_half_point_events']}|{p['second_half_point_events']}|")
        lines += ["", f"正式建议合计 {proposal['summary']['events']:,} 事件，{proposal['summary']['tasks']:,} 个独立任务。",
            f"按校准速度估计约 {proposal['estimated_core_hours']:.1f} 核时；保守资源估计 {proposal['conservative_core_hours']:.1f} 核时。",
            "每任务 100 事件、1 CPU、4 GiB、40core、1 小时；最大并发 4。",
            "如任务余量检查未通过，formal-plan/manifest.json 的 executable=false，不能直接运行。", "",
            "样本量使用 A 点估计与重采样 90% 分位的较大值，再加 20% 样本余量；不是精度保证。",
            "最终正式数据仍须检验四个区间的实际半宽是否均不超过 5 个百分点。",
            "前后半样本和尾部贡献用于评估规划稳定性；没有裁剪极端事件。",
            "零分母和退化重采样不得删除后继续计算。统计精度不包括材料和装配的系统误差。"]
        (output/"report.zh.md").write_text("\n".join(lines)+"\n")
        save_new(output/"calibration-result.json", result)
        save_new(output/"artifact-index.json", {p.relative_to(output).as_posix():osc.sha256(p)
            for p in sorted(output.rglob("*")) if p.is_file() and "plot-cache" not in p.parts})
        print(json.dumps({k:result[k] for k in ("passed", "calibration_events", "main_event_proposal",
                         "main_task_proposal", "estimated_main_core_hours", "main_submitted")}, indent=2))
    except Exception as error:
        save_new(output/"rejected.json", dict(passed=False, reason=str(error),
            main_submitted=False, executable_main_proposal=False, manifest_sha256=args.manifest_sha256))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    for flag in ("batch-dir", "source-manifest", "build-receipt", "benchmark-bundle", "benchmark-audit",
                 "qualification-report", "analysis-python"):
        p.add_argument("--"+flag, type=Path, required=True)
    p.add_argument("--account", required=True)
    p = sub.add_parser("estimate")
    p.add_argument("--batch-dir", type=Path, required=True)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    (prepare if args.command == "prepare" else estimate)(args)


if __name__ == "__main__":
    main()
