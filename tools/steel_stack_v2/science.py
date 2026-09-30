#!/usr/bin/env python3
"""Execute the approved frozen main sample; never resize it after seeing results."""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import numpy as np

import osc
from model import canonical_json, validate_tasks
from precision import csv_rows, serializable_group, plots
from precision_io import analysis_identity, event_block, read_campaign as read_calibration, save_new
from precision_math import SPEC, group_statistics, interval
from precision_worker import stop_campaign, verify_sibling_states
from science_io import (CONTROLLER_FILES, IDENTITIES, validate_proposal, read_campaign,
                        receipt_entry, scheduler_rows, require_completed)


def now(): return datetime.now(timezone.utc).isoformat()


def scheduler_snapshot(jobs):
    result = subprocess.run(["sacct","-j",",".join(jobs),"-X","--parsable2",
        "--format=JobID%40,State%40,ExitCode,AllocCPUS,ElapsedRaw,NodeList"],text=True,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,check=True,timeout=60)
    return result.stdout,scheduler_rows(result.stdout)


def auxiliary_script(root,python,account,label,minutes,arguments):
    return "\n".join(["#!/bin/bash", "#SBATCH --job-name="+label, "#SBATCH --account="+account,
        "#SBATCH --nodes=1", "#SBATCH --ntasks=1", "#SBATCH --cpus-per-task=1", "#SBATCH --mem=4G",
        "#SBATCH --constraint=40core", f"#SBATCH --time=00:{minutes:02d}:00" if minutes < 60 else "#SBATCH --time=01:00:00",
        "#SBATCH --no-requeue", "#SBATCH --output="+str(root/(label+"-%j.log")), "set -euo pipefail",
        "module load python/3.12",
        "export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1",
        "exec "+shlex.join([python,str(root/"controller/science.py"),*arguments])])+"\n"


def prepare(args):
    root = args.batch_dir.resolve()
    cal = args.calibration_dir.resolve()
    osc.require(not root.exists() and root != cal and cal not in root.parents, "Use an independent fresh main campaign")
    osc.require(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*",args.account) and
                re.fullmatch(r"[0-9a-f]{40}",args.controller_commit), "Invalid account/controller commit")
    osc.require(1 <= args.array_capacity <= min(1000,args.max_array_size-1), "Array capacity exceeds live scheduler limit")
    osc.require(Path(sys.executable).resolve() == args.analysis_python.resolve(), "Use the declared frozen analysis interpreter")
    cm,ct = read_calibration(cal/"bundle",args.calibration_manifest_sha256)
    a = cal/"analysis"
    osc.require(osc.sha256(a/"formal-plan/manifest.json") == args.proposal_sha256, "Frozen formal proposal changed")
    index = osc.read(a/"artifact-index.json")
    osc.require(all(osc.sha256(a/name) == h for name,h in index.items()), "Calibration analysis artifact changed")
    result = osc.read(a/"calibration-result.json")
    osc.require(result["passed"] and result["statistical_design_valid"] and result["calibration_events"] == 8000 and
                result["main_submitted"] is False, "Calibration did not complete successfully")
    entries = osc.read(a/"accepted-osc-index.json")["entries"]
    osc.require(len(entries) == 80, "Missing accepted calibration index")
    for e in entries:
        accepted = Path(e["accepted_receipt"])
        osc.require(osc.sha256(accepted) == e["accepted_receipt_sha256"] and
                    osc.sha256(accepted.parent/"calibration-audit.json") == e["audit_sha256"] and
                    osc.sha256(e["root"]) == e["root_sha256"], "Calibration evidence changed after proposal")
    proposal = osc.read(a/"formal-plan/manifest.json")
    tasks = osc.read(a/"formal-plan/tasks.json")
    osc.require(osc.sha256(a/"formal-plan/tasks.json") == proposal["tasks_sha256"], "Proposal task registry changed")
    summary = validate_proposal(proposal,tasks,ct,osc.read(cal/"excluded-benchmark-seeds.json"))
    osc.require(all(proposal["simulation_identity"][k] == cm[k] for k in IDENTITIES), "Formal simulation changed from calibration")
    osc.validate_source(cal/"prior-evidence/simulation-source-manifest.json",cm["remote_paths"]["source_root"])
    for key in ("executable","sif"):
        osc.require(osc.sha256(cm["remote_paths"][key]) == cm[key+"_sha256"], "Frozen simulation artifact changed")
    osc.require_x86_elf(Path(cm["remote_paths"]["executable"]))
    root.mkdir(parents=True)
    (root/"proposal").mkdir()
    for name,path in (("manifest.json",a/"formal-plan/manifest.json"),("tasks.json",a/"formal-plan/tasks.json"),
                      ("allocation.csv",a/"formal-plan/allocation.csv"),("calibration-result.json",a/"calibration-result.json"),
                      ("calibration-tasks.json",cal/"bundle/tasks.json"),
                      ("excluded-benchmark-seeds.json",cal/"excluded-benchmark-seeds.json")):
        shutil.copyfile(path,root/"proposal"/name)
    shutil.copyfile(cal/"statistics-spec.json",root/"statistics-spec.json")
    shutil.copyfile(a/"formal-plan/tasks.json",root/"tasks.json")
    shutil.copyfile(cal/"bundle/manifest.json",root/"calibration-manifest.json")
    controller = root/"controller"
    controller.mkdir()
    for name in CONTROLLER_FILES: shutil.copyfile(Path(__file__).parent/name,controller/name)
    save_new(controller/"controller.json",dict(schema_version="steel-main-controller-v1",commit=args.controller_commit,
        files={n:osc.sha256(controller/n) for n in CONTROLLER_FILES},analysis_identity=analysis_identity()))
    control = dict(root=str(controller),sha256=osc.sha256(controller/"controller.json"),
                   python=str(args.analysis_python.absolute()),stop_file=str(root/"STOP.json"))
    bundles = [dict(name=f"array-{i:03d}",path=str(root/"bundles"/f"array-{i:03d}"),offset=start,
                    tasks=min(args.array_capacity,len(tasks)-start))
               for i,start in enumerate(range(0,len(tasks),args.array_capacity))]
    frozen = {str(p.relative_to(root)):osc.sha256(p) for p in [root/"tasks.json",root/"statistics-spec.json",
        root/"calibration-manifest.json",*sorted((root/"proposal").iterdir())]}
    campaign = dict(schema_version="steel-gap-main-campaign-v1",purpose="science",root=str(root),prepared_at_utc=now(),
        execution_authorized=True,authorization="User approved completion, submission, polling, and final precision assessment of the frozen 247200-event main sample.",
        simulation_identity=proposal["simulation_identity"],controller_commit=args.controller_commit,control=control,
        resources=SPEC["resources"],summary=summary,array_capacity=args.array_capacity,
        observed_max_array_size=args.max_array_size,bundles=bundles,frozen_inputs=frozen,
        calibration_dir=str(cal),calibration_manifest_sha256=args.calibration_manifest_sha256,
        proposal_sha256=args.proposal_sha256,calibration_in_main=False,benchmark_in_main=False,
        additional_events_authorized=False,gap_selected=None,equivalence_tested=False)
    save_new(root/"campaign.json",campaign)
    campaign_hash = osc.sha256(root/"campaign.json")
    for info in bundles:
        plan = copy.deepcopy(cm)
        plan.pop("calibration_control",None)
        plan.update(purpose="science",tasks=tasks[info["offset"]:info["offset"]+info["tasks"]],
                    summary=validate_tasks(tasks[info["offset"]:info["offset"]+info["tasks"]]),
                    total_event_budget=100*info["tasks"],task_offset=info["offset"],campaign_seed=SPEC["main_seed"],
                    science_control={**control,"campaign_root":str(root),"campaign_sha256":campaign_hash})
        osc.render(plan,Path(info["path"]),remote_bundle_root=info["path"],remote_output_root=str(root/"results"),
                   account=args.account,time_minutes=60,memory_gib=4,max_parallel=4,node_constraint="40core")
        (Path(info["path"])/"continue.sbatch").write_text(auxiliary_script(root,control["python"],args.account,
            "g4-main-continue-"+info["name"],15,["advance","--batch-dir",str(root),"--campaign-sha256",campaign_hash]))
    (root/"analysis.sbatch").write_text(auxiliary_script(root,control["python"],args.account,"g4-main-analysis",60,
        ["analyze","--batch-dir",str(root),"--campaign-sha256",campaign_hash,"--output-dir",str(root/"analysis")]))
    files = [p for p in (root/"bundles").rglob("*") if p.is_file()]+[root/"analysis.sbatch"]
    save_new(root/"preparation.json",dict(campaign_sha256=campaign_hash,events=247200,tasks=2472,submitted=False,
        artifacts={str(p.relative_to(root)):osc.sha256(p) for p in sorted(files)}))
    read_campaign(root,campaign_hash)
    return dict(state="prepared-not-submitted",root=str(root),campaign_sha256=campaign_hash,
                array_sizes=[i["tasks"] for i in bundles],events=247200)


def submit_one(root,campaign_hash,key,script,*,dependency=None):
    directory = root/"submissions"
    directory.mkdir(exist_ok=True)
    receipt = directory/(key+".json")
    if receipt.exists():
        existing = osc.read(receipt)
        osc.require(existing["campaign_sha256"] == campaign_hash and existing["script_sha256"] == osc.sha256(script),
                    "Existing submission identity changed")
        return existing
    intent = directory/(key+".intent.json")
    osc.require(not intent.exists(), "Unresolved earlier submission intent: reconcile the scheduler before retrying "+key)
    command = ["sbatch","--parsable","--job-name=g4-main-"+campaign_hash[:12]+"-"+key]
    # Hold the array until both its identity receipt and continuation are saved.
    # Otherwise a fast scheduler can start a worker before its receipt exists.
    if key.startswith("array-"): command.append("--hold")
    if dependency: command.append("--dependency=afterany:"+dependency)
    command.append(str(script))
    document = dict(campaign_sha256=campaign_hash,key=key,script=str(script),script_sha256=osc.sha256(script),
                    command=command,dependency=dependency,intent_at_utc=now())
    save_new(intent,document)
    result = subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,timeout=60)
    save_new(directory/(key+".response.json"),dict(stdout=result.stdout,stderr=result.stderr,returncode=result.returncode))
    osc.require(result.returncode == 0 and re.fullmatch(r"[0-9]+(?:;[A-Za-z0-9_.-]+)?",result.stdout.strip()),
                "Submission rejected or uncertain; preserve intent and do not resubmit: "+result.stderr)
    document.update(job_id=result.stdout.strip().split(";")[0],submitted_at_utc=now(),stdout=result.stdout)
    save_new(receipt,document)
    return document


def release_array(root,campaign_hash,key,job):
    receipt = root/"submissions"/(key+".release.json")
    if receipt.exists():
        osc.require(osc.read(receipt)["job_id"] == job, "Array release belongs to another job")
        return
    state = subprocess.run(["scontrol","show","job",job,"--oneliner"],text=True,stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,check=True,timeout=30)
    if "Reason=JobHeldUser" in state.stdout:
        result = subprocess.run(["scontrol","release",job],text=True,stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,check=True,timeout=30)
        outcome = dict(stdout=result.stdout,stderr=result.stderr)
    else:
        osc.require(any("JobState="+s in state.stdout for s in
                        ("PENDING","RUNNING","COMPLETING","COMPLETED")), "Unexpected held/release scheduler state")
        outcome = dict(observed_already_released=True,state=state.stdout)
    save_new(receipt,dict(job_id=job,campaign_sha256=campaign_hash,released_at_utc=now(),**outcome))


def completed_array(campaign,tasks,info,job,*,revalidate=False):
    text,rows = scheduler_snapshot([job])
    require_completed(rows,job,info["tasks"])
    selected = tasks[info["offset"]:info["offset"]+info["tasks"]]
    entries = [receipt_entry(campaign,info,t,i,job,revalidate=revalidate) for i,t in enumerate(selected,1)]
    return text,entries


def advance(args):
    root = args.batch_dir.resolve()
    campaign,tasks = read_campaign(root,args.campaign_sha256)
    osc.require(not(root/"STOP.json").exists(), "Formal campaign stopped; no later array may start")
    lock = root/".submission-lock"
    descriptor = os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(descriptor)
    try:
        for info in campaign["bundles"]:
            receipt = root/"submissions"/(info["name"]+".json")
            if not receipt.exists():
                job = submit_one(root,args.campaign_sha256,info["name"],Path(info["path"])/"array.sbatch")
                follow = submit_one(root,args.campaign_sha256,"continue-"+info["name"],Path(info["path"])/"continue.sbatch",
                                    dependency=job["job_id"])
                release_array(root,args.campaign_sha256,info["name"],job["job_id"])
                return dict(state="array-submitted",array=info["name"],job=job["job_id"],continuation=follow["job_id"],
                            events=100*info["tasks"],total_authorized_events=247200)
            job = osc.read(receipt)["job_id"]
            follow = root/"submissions"/("continue-"+info["name"]+".json")
            if not follow.exists():
                submit_one(root,args.campaign_sha256,"continue-"+info["name"],Path(info["path"])/"continue.sbatch",dependency=job)
            release_array(root,args.campaign_sha256,info["name"],job)
            text,rows = scheduler_snapshot([job])
            verify_sibling_states("\n".join("|".join((r["JobID"],r["State"],r["ExitCode"])) for r in rows.values()))
            if not all(rows.get(job+"_"+str(i),{}).get("State") == "COMPLETED" for i in range(1,info["tasks"]+1)):
                return dict(state="waiting-for-array",array=info["name"],job=job)
            text,entries = completed_array(campaign,tasks,info,job)
            completion = root/"submissions"/(info["name"]+".completion.json")
            if not completion.exists():
                save_new(completion,dict(job_id=job,passed=True,events=sum(e["task"]["events"] for e in entries),
                    campaign_sha256=args.campaign_sha256,accounting=text,
                    accepted_receipts={e["task"]["task_id"]:e["accepted_receipt_sha256"] for e in entries}))
        analysis = submit_one(root,args.campaign_sha256,"analysis",root/"analysis.sbatch")
        return dict(state="analysis-submitted",job=analysis["job_id"],formal_events=247200)
    except BaseException as error:
        stop_campaign(root/"STOP.json",error,0)
        raise
    finally:
        lock.unlink()


def status(args):
    root = args.batch_dir.resolve()
    campaign,tasks = read_campaign(root,args.campaign_sha256)
    receipts = sorted((root/"submissions").glob("array-???.json"))
    jobs = [osc.read(p)["job_id"] for p in receipts]
    controls = [osc.read(p)["job_id"] for p in sorted((root/"submissions").glob("continue-array-???.json"))]
    if (root/"submissions/analysis.json").exists():
        controls.append(osc.read(root/"submissions/analysis.json")["job_id"])
    text,rows = scheduler_snapshot(jobs+controls) if jobs or controls else ("",{})
    accepted = list(root.glob("results/*/job-*/accepted.json"))
    registry = {t["task_id"]:t for t in tasks}
    observed = set()
    for p in accepted:
        r = osc.read(p)
        osc.require(r["task_id"] in registry and r["task_id"] not in observed and r["events"] == 100 and
                    r["registered_task_sha256"] == hashlib.sha256(canonical_json(registry[r["task_id"]])).hexdigest() and
                    r["accepted_for_main"] is True and r["accepted_for_calibration"] is False and
                    r["campaign_sha256"] == args.campaign_sha256 and
                    osc.sha256(p.parent/"science-audit.json") == r["audit_sha256"] and
                    osc.sha256(p.parent/"execution.json") == r["execution_sha256"], "Invalid progress receipt")
        observed.add(r["task_id"])
    failures = {k:r for k,r in rows.items() if r["State"].split()[0].rstrip("+") in
                ("FAILED","NODE_FAIL","OUT_OF_MEMORY","TIMEOUT","CANCELLED","PREEMPTED","BOOT_FAIL")}
    if failures: stop_campaign(root/"STOP.json",str(failures),0)
    summary = dict(observed_at_utc=now(),campaign_sha256=args.campaign_sha256,total_events=247200,total_tasks=2472,
        submitted_arrays=len(jobs),array_jobs=jobs,accepted_tasks=len(accepted),accepted_events=100*len(accepted),
        control_job_states={k:r for k,r in rows.items() if k in controls},
        task_states=dict(Counter(r["State"] for k,r in rows.items() if "_" in k)),
        state="stopped" if (root/"STOP.json").exists() else "running" if jobs else "prepared-not-submitted",
        failures=failures,formal_precision_assessed=False)
    if (root/"STOP.json").exists(): summary["stop"] = osc.read(root/"STOP.json")
    if (root/"analysis/main-result.json").exists():
        result = osc.read(root/"analysis/main-result.json")
        inventory = osc.read(root/"analysis/artifact-index.json")
        osc.require(all(osc.sha256(root/"analysis"/n) == h for n,h in inventory.items()), "Final analysis artifact changed")
        summary.update(state="complete",formal_precision_assessed=True,result=result)
    elif (root/"submissions/analysis.json").exists():
        job = osc.read(root/"submissions/analysis.json")["job_id"]
        _,analysis_rows = scheduler_snapshot([job])
        summary["analysis_job"] = job
        summary["analysis_states"] = analysis_rows
        if any(r["State"].split()[0].rstrip("+") in ("FAILED","OUT_OF_MEMORY","TIMEOUT","CANCELLED","NODE_FAIL") for r in analysis_rows.values()):
            stop_campaign(root/"STOP.json","Final analysis scheduler failure: "+str(analysis_rows),0)
            summary["state"] = "stopped"
        elif len(accepted) == 2472: summary["state"] = "awaiting-analysis"
    (root/"status").mkdir(exist_ok=True)
    save_new(root/"status"/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+".json"),summary)
    return summary


def assess_pair(reference,candidate,*,thickness,layout):
    a,b = reference["statistics"]["mean"],candidate["statistics"]["mean"]
    x,y = reference["bootstrap_mean"],candidate["bootstrap_mean"]
    samples = np.divide(y,x,out=np.full(len(x),np.nan),where=x>0)-1
    point = b/a-1 if a>0 else np.nan
    corrected = interval(point,samples,SPEC["corrected_quantiles"])
    return dict(tile_thickness_mm=thickness,layout=layout,reference_gap_mm=.5,candidate_gap_mm=1.,
        main_relative_change=float(point) if np.isfinite(point) else None,
        pointwise95=interval(point,samples,SPEC["pointwise_quantiles"]),corrected9875=corrected,
        target_half_width=.05,precision_passed=corrected["valid"] and corrected["half_width"] <= .05,
        calibration_included=False,benchmark_included=False,equivalence_tested=False)


def contrast_plot(pairs,output):
    import matplotlib.pyplot as plt
    fig,ax = plt.subplots(figsize=(9,5),constrained_layout=True)
    for i,p in enumerate(pairs):
        for key,offset,color in (("corrected9875",.08,"#275d87"),("pointwise95",-.08,"#999999")):
            ci = p[key]
            if ci["valid"]:
                ax.plot([100*ci["low"],100*ci["high"]],[i+offset]*2,color=color,linewidth=3)
                ax.plot(100*p["main_relative_change"],i+offset,"o",color=color)
    ax.axvline(0,color="black",linewidth=.7)
    ax.set(yticks=range(4),yticklabels=[f"t={p['tile_thickness_mm']} mm, {p['layout']}" for p in pairs],
           xlabel="Response change: gap 1.0 / 0.5 - 1 (%)",
           title="Independent formal sample: blue 98.75%, gray pointwise 95%")
    for extension in ("png","pdf"): fig.savefig(output/("gap-comparisons."+extension),dpi=160)
    plt.close(fig)


def analyze(args):
    root = args.batch_dir.resolve()
    campaign,tasks = read_campaign(root,args.campaign_sha256)
    output = args.output_dir.resolve()
    osc.require(output == root/"analysis" and not output.exists(), "Use the fresh campaign analysis directory")
    osc.require(not(root/"STOP.json").exists(), "Stopped campaign cannot enter formal statistics")
    output.mkdir()
    try:
        entries,accounting = [],[]
        for info in campaign["bundles"]:
            submission = osc.read(root/"submissions"/(info["name"]+".json"))
            text,e = completed_array(campaign,tasks,info,submission["job_id"],revalidate=True)
            entries.extend(e);accounting.append(text)
        osc.require(len(entries) == 2472 and sum(e["task"]["events"] for e in entries) == 247200,
                    "Missing main events; no zero padding or partial estimates")
        osc.require({p.name for p in (root/"results").iterdir() if p.is_dir()} == {t["task_id"] for t in tasks},
                    "Unregistered formal result directories")
        (output/"scheduler-accounting.txt").write_text("\n".join(accounting))
        save_new(output/"accepted-osc-index.json",dict(purpose="science",campaign_sha256=args.campaign_sha256,entries=entries))
        grouped = defaultdict(list)
        for e in entries:
            block = event_block(e);cfg = block["config"]
            grouped[(cfg["tile_thickness_mm"],cfg["layout"],cfg["gap_mm"])].append(block)
        groups,diagnostics,blocks = {},[],[]
        for key,data in sorted(grouped.items()):
            fields = data[0]["fields"]
            osc.require(all(b["fields"] == fields for b in data), "Formal columns changed within configuration")
            expected = next(r["events"] for r in osc.read(root/"proposal/manifest.json")["allocation"]
                            if (r["tile_thickness_mm"],r["layout"],r["gap_mm"]) == key)
            osc.require(sum(len(b["values"]) for b in data) == expected, "Formal configuration event count differs")
            g = group_statistics(data,fields);groups[key]=g
            ident = dict(tile_thickness_mm=key[0],layout=key[1],gap_mm=key[2])
            diagnostics.append(dict(ident,**g["statistics"]))
            blocks.extend(dict(ident,**b) for b in g["blocks"])
            label = f"t{int(key[0]):02d}-{key[1]}-g{key[2]:g}"
            save_new(output/(label+"-main.json"),dict(ident,**serializable_group(g),
                response_mean95=interval(g["statistics"]["mean"],g["bootstrap_mean"],SPEC["pointwise_quantiles"])))
            np.savez_compressed(output/(label+"-bootstrap.npz"),mean=g["bootstrap_mean"],variance=g["bootstrap_variance"],
                                sums=g["bootstrap_sums"],fields=np.asarray(fields))
        csv_rows(output/"configuration-diagnostics.csv",diagnostics);csv_rows(output/"independent-blocks.csv",blocks)
        plots(groups,output,sample_label="Independent formal sample")
        pairs = [assess_pair(groups[(t,l,.5)],groups[(t,l,1.)],thickness=t,layout=l) for t,l in SPEC["pairs"]]
        save_new(output/"four-comparisons.json",pairs);contrast_plot(pairs,output)
        result = dict(audit_passed=True,formal_events=247200,formal_tasks=2472,configurations=8,
            statistics_valid=all(p["corrected9875"]["valid"] for p in pairs),
            precision_passed=all(p["precision_passed"] for p in pairs),target_half_width=.05,
            nominal_family_confidence=.95,analysis_seed=SPEC["analysis_seed"],resamples=10000,
            pairs=pairs,campaign_sha256=args.campaign_sha256,calibration_included=False,benchmark_included=False,
            numerical_corrections=sum(e["numerical"]["corrections"] for e in entries),
            no_rindex=sum(e["numerical"]["boundary_no_rindex"] for e in entries),
            additional_events_run=False,equivalence_tested=False,gap_selected=None)
        lines=["# 八配置间隙正式扫描：完成与精度检查", "",
            "247,200 个独立正式中子事件、2,472 项任务通过审计；校准和 benchmark 未并入。",
            "主指标是包含零响应的全模块收光／入射中子，四项都以 0.5 mm 为参照。",
            "完整事件在各 100 事件任务块内重采 10,000 次；分析种子 2026093002。",
            "98.75% 单项区间经 Bonferroni 对应名义整体 95%；目标半宽为 5 个百分点。", "",
            "|厚度 mm|布局|相对变化 %|校正区间 %|半宽 百分点|达到精度|", "|---:|---|---:|---|---:|---|"]
        for p in pairs:
            ci=p["corrected9875"]
            if ci["valid"]:
                lines.append(f"|{p['tile_thickness_mm']}|{p['layout']}|{100*p['main_relative_change']:.4f}|[{100*ci['low']:.4f}, {100*ci['high']:.4f}]|{100*ci['half_width']:.4f}|{p['precision_passed']}|")
            else: lines.append(f"|{p['tile_thickness_mm']}|{p['layout']}|无效|无效重采样 {ci['invalid_resamples']} 项|无效|False|")
        lines += ["", "四项精度全部达到目标："+str(result["precision_passed"])+"。",
            "没有自动追加事件。区间包含零不证明等价，统计精度不包括材料和装配系统误差。",
            "逐配置、逐层、逐有效传感器、产光、能量沉积和零响应率辅助统计与 bootstrap 记录已归档。",
            "冻结任务、独立种子和实际 OSC 输出由 accepted-osc-index.json 与校验清单绑定。"]
        (output/"report.zh.md").write_text("\n".join(lines)+"\n")
        save_new(output/"main-result.json",result)
        save_new(output/"artifact-index.json",{str(p.relative_to(output)):osc.sha256(p) for p in sorted(output.rglob("*"))
            if p.is_file() and "plot-cache" not in p.parts})
        return result
    except BaseException as error:
        save_new(output/"rejected.json",dict(reason=str(error),audit_passed=False,additional_events_run=False))
        stop_campaign(root/"STOP.json",error,0)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest="command",required=True)
    p=sub.add_parser("prepare")
    for flag in ("batch-dir","calibration-dir","analysis-python"):p.add_argument("--"+flag,type=Path,required=True)
    for flag in ("calibration-manifest-sha256","proposal-sha256","controller-commit","account"):p.add_argument("--"+flag,required=True)
    p.add_argument("--max-array-size",type=int,required=True);p.add_argument("--array-capacity",type=int,default=900)
    for action in ("advance","status","analyze"):
        p=sub.add_parser(action);p.add_argument("--batch-dir",type=Path,required=True);p.add_argument("--campaign-sha256",required=True)
        if action == "analyze":p.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(globals()[args.command](args),indent=2,allow_nan=False))


if __name__ == "__main__":main()
