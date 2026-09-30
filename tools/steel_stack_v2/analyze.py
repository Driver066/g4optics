#!/usr/bin/env python3
"""Analyze accepted v2 ledgers using whole-event, within-block bootstrap.

Engineering acceptance is opt-in and never promoted to scientific evidence.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import itertools
import json
import math
import os
from pathlib import Path

import numpy as np
import uproot

from audit import TREES, FATES, ENERGY_FIELDS, read_json, sha256, verify_file_hash, tree_arrays
from model import LAYOUTS, canonical_json, configuration_hash, require, validate_configuration

REPLICAS = 10000
BOOTSTRAP_SEED = 20260929


def resolve(path, base):
    path = Path(path)
    return path if path.is_absolute() else base/path


def safe_ratio(numerator, denominator):
    numerator, denominator = np.broadcast_arrays(np.asarray(numerator,dtype=float), np.asarray(denominator,dtype=float))
    valid = np.isfinite(numerator) & np.isfinite(denominator) & (denominator != 0)
    values = np.full(numerator.shape, np.nan)
    np.divide(numerator, denominator, out=values, where=valid)
    return values, valid


def estimate(central, replicates):
    replicates = np.asarray(replicates,dtype=float)
    finite = np.isfinite(replicates)
    valid = bool(np.isfinite(central))
    lo, hi = (np.quantile(replicates[finite],[.025,.975]) if valid and finite.any() else [np.nan,np.nan])
    return {"estimate": float(central), "valid": valid, "ci95_low": float(lo), "ci95_high": float(hi),
            "valid_resamples": int(finite.sum()), "total_resamples": len(replicates),
            "interval_conditional_on_valid_denominator": bool(finite.sum() != len(replicates))}


def stable_rng(identity, seed=BOOTSTRAP_SEED):
    words=np.frombuffer(hashlib.sha256(identity.encode()).digest()[:16],dtype="<u4")
    return np.random.default_rng(np.random.SeedSequence([seed,*map(int,words)]))


def event_weights(tasks, replicas=REPLICAS, seed=BOOTSTRAP_SEED):
    return np.concatenate([stable_rng(task["task_id"],seed).multinomial(int(task["events"]),
        np.full(int(task["events"]),1/int(task["events"])),size=replicas) for task in tasks],axis=1).astype(float)


def select_tasks(tasks, engineering=False):
    selected=[]
    for task in tasks:
        config=task.get("config",{})
        if config.get("study_preset") != "steel-module-stack-v2":continue
        if not task.get("accounting_enabled",task.get("accounting",True)):continue
        if task.get("repeat_of") or task.get("reproducibility_check") or task.get("stage")=="repeat":continue
        purpose=str(task.get("purpose",task.get("specificpurpose",""))).lower()
        if engineering:
            # The 48 one-event smokes are an explicit complete engineering matrix.
            # Off/on observer pairs are not additional independent smoke evidence.
            if task.get("stage") != "smoke" or "engineering" not in purpose:continue
        else:
            if "engineering" in purpose or task.get("independent_sample") is not True:continue
            if task.get("stage") not in ("science","pilot","production"):continue
        selected.append(task)
    require(selected, "No eligible accounting-on tasks for this analysis mode")
    require(len({task["task_id"] for task in selected})==len(selected),"Duplicate selected task IDs")
    pairs=[(task["seed1"],task["seed2"]) for task in selected]
    require(len(set(pairs))==len(pairs),"Selected samples reuse seed pairs")
    return selected


def verify_task(task,batch,registered):
    config=validate_configuration(task["config"])
    require(task.get("accepted") is True,"Task is not accepted: "+task["task_id"])
    require(task["task_id"] in registered,"Task is absent from the frozen registry")
    original=registered[task["task_id"]]
    require(all(task.get(key)==value for key,value in original.items()),"Registered scientific task identity changed")
    run=resolve(task["run_dir"],batch).resolve()
    require(run.is_relative_to((batch/"runs"/task["task_id"]).resolve()),"Run path escapes its registered task")
    receipt_path=resolve(task["receipt"],batch)
    verify_file_hash(receipt_path,task["receipt_sha256"])
    receipt=read_json(receipt_path)
    require(receipt.get("accepted") is True and receipt.get("task_id")==task["task_id"] and
            receipt.get("events")==task["events"] and Path(receipt.get("run_dir","")).resolve()==run,
            "Accepted receipt identity differs")
    verify_file_hash(batch/"manifest.json",receipt["manifest_sha256"])
    require(receipt["registered_task_sha256"]==hashlib.sha256(canonical_json(original)).hexdigest(),"Receipt registry binding differs")
    role=task["role"]
    require(receipt.get("role")==role,"Receipt source role differs")
    binary=read_json(batch/role/"binary.json")
    require(receipt["executable_sha256"]==binary["sha256"],"Receipt binary identity differs")
    verify_file_hash(batch/role/"source-manifest.json",receipt["source_manifest_sha256"])
    audit_path=resolve(task["audit_path"],batch)
    verify_file_hash(audit_path,receipt["audit_sha256"])
    audit=read_json(audit_path)
    require(audit.get("passed") is True and audit.get("accounting_enabled") is True,"Accepted accounting audit missing")
    require(audit["configuration_hash"]==configuration_hash(config) and audit["events"]==task["events"],"Audit task/config identity differs")
    for relative,expected in receipt["artifacts"].items():
        path=(run/relative).resolve();require(path.is_relative_to(run),"Artifact path escapes run")
        verify_file_hash(path,expected)
    for relative,entry in audit["files"].items():
        path=(run/relative).resolve();require(path.is_relative_to(run),"Audit artifact path escapes run")
        verify_file_hash(path,entry["sha256"])
    roots=list((run/"outputs").glob("*.root"));require(len(roots)==1,"Expected one audited ROOT")
    return roots[0],{"task_id":task["task_id"],"root_sha256":sha256(roots[0]),"receipt_sha256":sha256(receipt_path),
                     "audit_sha256":sha256(audit_path),"events":task["events"]}


def write_csv(path,rows):
    require(rows,"Empty table: "+str(path))
    fields=list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def identity(config):
    return {"configuration_hash":configuration_hash(config),"optical_numerics":config.get("optical_numerics", {"profile":"legacy","scale":0}),"layout":config["layout"],
            "tile_thickness_mm":config["tile_thickness_mm"],"gap_mm":config["gap_mm"]}


def add_estimate(record,name,value,replicates):
    result=estimate(value,replicates)
    record.update({name if key=="estimate" else name+"_"+key:val for key,val in result.items()})


def read_group(tasks,paths):
    events=[];layers=[];sensors=[];flows=[];offset=0
    for task,path in zip(tasks,paths):
        with uproot.open(path) as root:
            e,l,s,f=(tree_arrays(root[name]) for name in TREES)
        event_order=np.argsort(e["event_id"]);e={key:value[event_order] for key,value in e.items()}
        layer_order=np.lexsort((l["layer"],l["event_id"]));l={key:value[layer_order] for key,value in l.items()}
        sensor_order=np.lexsort((s["local_sensor"],s["layer"],s["event_id"]));s={key:value[sensor_order] for key,value in s.items()}
        f["event_id"]=f["event_id"].astype(int)+offset
        f["creator_process"]=f["creator_process"].astype(str)
        events.append(e);layers.append(l);sensors.append(s);flows.append(f);offset+=task["events"]
    concatenate=lambda pieces:{key:np.concatenate([part[key] for part in pieces]) for key in pieces[0]}
    return tuple(concatenate(pieces) for pieces in (events,layers,sensors,flows))


def summarize_group(tasks,paths):
    config=tasks[0]["config"];ident=identity(config)
    event,layer,sensor,flow=read_group(tasks,paths)
    n=sum(task["events"] for task in tasks);k=config["sensors_per_layer"]
    weights=event_weights(tasks)
    # The primary new denominator is *tile-born tracks*, not all world births.
    # Collection can happen at any destination layer/sensor. Counting only
    # same-birth-layer detections here would incorrectly discard transfers.
    tile_collected=(flow["birth_class"]==1)&(flow["fate"]==0)
    event["detected_tile_birth"]=np.bincount(flow["event_id"].astype(int),
        weights=flow["photon_count"]*tile_collected,minlength=n)
    event["generated_tile_legacy"]=layer["generated_legacy"].reshape(n,10).sum(1)
    event["detected_same_root_layer"]=sensor["detected_same_root_layer"].reshape(n,10,k).sum((1,2))
    keycols=["generated_legacy","generated_tile_legacy","detected_legacy","detected_tile_birth","detected_same_root_layer",
             "births_total","births_tile","births_primary","births_nonoptical_parent","births_optical_parent",
             "births_scintillation","births_cerenkov","births_wls","births_wls2","births_other",*ENERGY_FIELDS]
    matrix=np.column_stack([event[key] for key in keycols]).astype(float)
    sums=matrix.sum(0);resamples=weights@matrix
    total=dict(zip(keycols,sums));boot={key:resamples[:,i] for i,key in enumerate(keycols)}
    central={"net":total["detected_legacy"]/n,"legacy_ce":safe_ratio(total["detected_legacy"],total["generated_legacy"])[0].item(),
             "birth_ce":safe_ratio(total["detected_tile_birth"],total["births_tile"])[0].item(),
             "all_birth_ce":safe_ratio(total["detected_legacy"],total["births_total"])[0].item(),
             "same_root_legacy_ce":safe_ratio(total["detected_same_root_layer"],total["generated_tile_legacy"])[0].item(),
             "births_per_event":total["births_total"]/n,"legacy_generated_per_event":total["generated_legacy"]/n,
             "nonoptical_parent_births_per_event":total["births_nonoptical_parent"]/n,
             "optical_parent_births_per_event":total["births_optical_parent"]/n,
             "tile_nonoptical_edep_per_event":total["tile_nonoptical_edep_mev"]/n,
             "tile_optical_edep_per_event":total["tile_optical_edep_mev"]/n,
             "net_per_sensor":total["detected_legacy"]/n/config["sensor_count"],
             "net_per_active_mm2":total["detected_legacy"]/n/(config["sensor_count"]*2.4*2.4)}
    metrics={"net":boot["detected_legacy"]/n,"legacy_ce":safe_ratio(boot["detected_legacy"],boot["generated_legacy"])[0],
             "birth_ce":safe_ratio(boot["detected_tile_birth"],boot["births_tile"])[0],
             "all_birth_ce":safe_ratio(boot["detected_legacy"],boot["births_total"])[0],
             "same_root_legacy_ce":safe_ratio(boot["detected_same_root_layer"],boot["generated_tile_legacy"])[0],
             "births_per_event":boot["births_total"]/n,"legacy_generated_per_event":boot["generated_legacy"]/n,
             "nonoptical_parent_births_per_event":boot["births_nonoptical_parent"]/n,
             "optical_parent_births_per_event":boot["births_optical_parent"]/n,
             "tile_nonoptical_edep_per_event":boot["tile_nonoptical_edep_mev"]/n,
             "tile_optical_edep_per_event":boot["tile_optical_edep_mev"]/n,
             "net_per_sensor":boot["detected_legacy"]/n/config["sensor_count"],
             "net_per_active_mm2":boot["detected_legacy"]/n/(config["sensor_count"]*2.4*2.4)}
    summary=dict(ident,events=n,blocks=len(tasks),sensors=config["sensor_count"],whole_event_bootstrap=True,
                 single_event_engineering_degeneracy=n==1)
    for name in central:add_estimate(summary,name,central[name],metrics[name])
    layer_rows=[];sensor_rows=[];fate_rows=[]
    for fate in FATES:
        value=event["fate_"+fate].astype(float);rep=weights@value
        row=dict(ident,fate=fate,photons=int(value.sum()))
        add_estimate(row,"per_event",value.sum()/n,rep/n)
        add_estimate(row,"fraction_of_births",safe_ratio(value.sum(),total["births_total"])[0].item(),safe_ratio(rep,boot["births_total"])[0])
        fate_rows.append(row)
    for index in range(10):
        cols={name:layer[name].reshape(n,10)[:,index].astype(float) for name in
              ("generated_legacy","births_total","detected_all_origins","detected_same_root_layer","detected_same_birth_layer",*ENERGY_FIELDS)}
        sums_l={name:values.sum() for name,values in cols.items()};reps_l={name:weights@values for name,values in cols.items()}
        row=dict(ident,layer=index,events=n)
        for name,numerator,denominator in (("net","detected_all_origins",None),
                                           ("legacy_ce","detected_same_root_layer","generated_legacy"),
                                           ("all_origin_legacy_ce","detected_all_origins","generated_legacy"),
                                           ("birth_ce","detected_same_birth_layer","births_total"),
                                           ("same_root_net","detected_same_root_layer",None)):
            if denominator is None:value=sums_l[numerator]/n;rep=reps_l[numerator]/n
            else:value=safe_ratio(sums_l[numerator],sums_l[denominator])[0].item();rep=safe_ratio(reps_l[numerator],reps_l[denominator])[0]
            add_estimate(row,name,value,rep)
        for name in ENERGY_FIELDS:add_estimate(row,name+"_per_event",sums_l[name]/n,reps_l[name]/n)
        layer_rows.append(row)
        for local in range(k):
            cols_s={name:sensor[name].reshape(n,10,k)[:,index,local].astype(float) for name in
                    ("detected_all_origins","detected_same_root_layer","detected_same_birth_layer","detected_root_outside","detected_birth_outside")}
            sums_s={name:v.sum() for name,v in cols_s.items()};reps_s={name:weights@v for name,v in cols_s.items()}
            row=dict(ident,layer=index,local_sensor=local,global_copy=4*index+local,events=n)
            for name,numerator,denominator in (("net","detected_all_origins",None),
                                               ("legacy_ce","detected_same_root_layer","generated_legacy"),
                                               ("all_origin_legacy_ce","detected_all_origins","generated_legacy"),
                                               ("birth_ce","detected_same_birth_layer","births_total"),
                                               ("same_root_net","detected_same_root_layer",None),
                                               ("outside_root_net","detected_root_outside",None),
                                               ("outside_birth_net","detected_birth_outside",None)):
                if denominator is None:value=sums_s[numerator]/n;rep=reps_s[numerator]/n
                else:value=safe_ratio(sums_s[numerator],sums_l[denominator])[0].item();rep=safe_ratio(reps_s[numerator],reps_l[denominator])[0]
                add_estimate(row,name,value,rep)
            sensor_rows.append(row)
    groups=defaultdict(lambda:np.zeros(n));transfer=defaultdict(lambda:np.zeros(n))
    for ix in range(len(flow["event_id"])):
        key=tuple(flow[name][ix].item() if hasattr(flow[name][ix],"item") else flow[name][ix]
                  for name in ("root_class","root_layer","birth_class","birth_layer","creator_process","parent_optical"))
        e=int(flow["event_id"][ix]);count=int(flow["photon_count"][ix]);groups[key][e]+=count
        if flow["fate"][ix]==0:
            transfer[(int(flow["root_class"][ix]),int(flow["root_layer"][ix]),int(flow["birth_class"][ix]),
                      int(flow["birth_layer"][ix]),int(flow["sensor_copy"][ix]))][e]+=count
    origins=[];transfers=[]
    for key,value in sorted(groups.items()):
        row=dict(ident,**dict(zip(("root_class","root_layer","birth_class","birth_layer","creator_process","parent_optical"),key)),photons=int(value.sum()))
        rep=weights@value;add_estimate(row,"per_event",value.sum()/n,rep/n)
        add_estimate(row,"fraction_of_births",safe_ratio(value.sum(),total["births_total"])[0].item(),safe_ratio(rep,boot["births_total"])[0]);origins.append(row)
    for key,value in sorted(transfer.items()):
        row=dict(ident,**dict(zip(("root_class","root_layer","birth_class","birth_layer","sensor_copy"),key)),photons=int(value.sum()))
        rep=weights@value;add_estimate(row,"net",value.sum()/n,rep/n)
        add_estimate(row,"fraction_of_detected",safe_ratio(value.sum(),total["detected_legacy"])[0].item(),safe_ratio(rep,boot["detected_legacy"])[0]);transfers.append(row)
    if not origins:origins=[dict(ident,no_birth_records=True,photons=0,per_event=0.,fraction_of_births=float("nan"),fraction_of_births_valid=False)]
    if not transfers:transfers=[dict(ident,no_detection_records=True,photons=0,net=0.,fraction_of_detected=float("nan"),fraction_of_detected_valid=False)]
    return summary,metrics,layer_rows,sensor_rows,fate_rows,origins,transfers


def comparisons(summaries,boot):
    result=[]
    for left,right in itertools.combinations(summaries,2):
        same_gap=left["gap_mm"]==right["gap_mm"]
        same_layout=left["layout"]==right["layout"]
        if left["tile_thickness_mm"]!=right["tile_thickness_mm"] or not (same_gap != same_layout):continue
        for metric in ("net","legacy_ce","birth_ce","net_per_sensor","net_per_active_mm2"):
            a,b=left[metric],right[metric];ra=boot[left["configuration_hash"]][metric];rb=boot[right["configuration_hash"]][metric]
            row={"reference_configuration":left["configuration_hash"],"candidate_configuration":right["configuration_hash"],
                 "reference_layout":left["layout"],"candidate_layout":right["layout"],"reference_gap_mm":left["gap_mm"],
                 "candidate_gap_mm":right["gap_mm"],"tile_thickness_mm":left["tile_thickness_mm"],"metric":metric,
                 "comparison_axis":"layout" if same_gap else "gap"}
            add_estimate(row,"difference",b-a,rb-ra)
            add_estimate(row,"ratio",safe_ratio(b,a)[0].item(),safe_ratio(rb,ra)[0])
            row["equivalence_tested"]=False;result.append(row)
    return result


def plot_summaries(rows,output,engineering):
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg
    colors={"back-center":"#1f77b4","edge-two":"#e69f00","back-two":"#666666","back-four":"#009e73"}
    markers={"back-center":"o","edge-two":"s","back-two":"^","back-four":"D"}
    styles={"back-center":"-","edge-two":"--","back-two":"-.","back-four":":"}
    gaps=sorted({row["gap_mm"] for row in rows})
    fig,axes=plt.subplots(3,len(gaps),figsize=(6*len(gaps),11),squeeze=False)
    fig.subplots_adjust(left=.09,right=.98,top=.88,bottom=.13,wspace=.3,hspace=.36)
    for col,gap in enumerate(gaps):
        for line,(metric,label) in enumerate((("net","Collected photons / neutron"),("legacy_ce","All-origin D / global legacy G"),("birth_ce","Collected tile-born tracks / tile births"))):
            ax=axes[line,col]
            for layout in LAYOUTS:
                selected=sorted((row for row in rows if row["gap_mm"]==gap and row["layout"]==layout and row[metric+"_valid"]),key=lambda row:row["tile_thickness_mm"])
                if not selected:continue
                x=[row["tile_thickness_mm"] for row in selected];y=[row[metric] for row in selected]
                ax.plot(x,y,label=layout,color=colors[layout],marker=markers[layout],linestyle=styles[layout])
                if not engineering:ax.vlines(x,[row[metric+"_ci95_low"] for row in selected],[row[metric+"_ci95_high"] for row in selected],color=colors[layout])
            ax.set(xlabel="Tile thickness (mm)",ylabel=label,title=f"Explicit common gap g = {gap:g} mm")
            ax.grid(alpha=.2);ax.set_ylim(bottom=0)
    handles,labels=axes[0,0].get_legend_handles_labels()
    if handles:fig.legend(handles,labels,loc="upper center",bbox_to_anchor=(.5,.94),ncol=4,frameon=False)
    title="Engineering acceptance outputs — not scientific evidence" if engineering else "Ten-layer stack comparison with declared common installation gaps"
    fig.suptitle(title,fontsize=15,y=.985)
    note=("Engineering smoke: one event/configuration; no uncertainty or ranking claim. Invalid zero-denominator values are omitted and flagged in CSV."
          if engineering else "Intervals: 10,000 whole-event resamples within each fixed block; pointwise 95%, unadjusted. Invalid denominator replicas are counted explicitly.")
    fig.text(.06,.03,note,fontsize=9,wrap=True)
    result=[]
    for ext in ("png","pdf"):
        path=output/f"stack-comparison.{ext}";fig.savefig(path,dpi=180)
        require(path.stat().st_size>1000,"Empty plot")
        if ext=="png":require(float(mpimg.imread(path)[...,:3].std())>.01,"Blank PNG")
        else:require(path.read_bytes().startswith(b"%PDF-"),"Invalid PDF")
        result.append(str(path))
    plt.close(fig);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir",required=True,type=Path);parser.add_argument("--output-dir",type=Path)
    parser.add_argument("--engineering",action="store_true")
    args=parser.parse_args();batch=args.batch_dir.resolve();output=args.output_dir.resolve() if args.output_dir else batch/"analysis"
    require(not output.exists(),"Analysis output already exists; use a separate --output-dir")
    manifest=read_json(batch/"manifest.json");verify_file_hash(batch/"registered-tasks.json",manifest["registered_tasks_sha256"])
    originals=read_json(batch/"registered-tasks.json");registered={task["task_id"]:task for task in originals}
    require(len(registered)==len(originals),"Duplicate frozen task IDs")
    tasks=select_tasks(read_json(batch/"tasks.json"),args.engineering)
    require(len({canonical_json(t["config"].get("optical_numerics", {"profile":"legacy","scale":0})) for t in tasks})==1, "Cannot combine numerical baselines in one scientific analysis")
    groups=defaultdict(list);paths={};evidence=[]
    for task in tasks:
        path,entry=verify_task(task,batch,registered);paths[task["task_id"]]=path;evidence.append(entry)
        groups[configuration_hash(task["config"])].append(task)
    output.mkdir(parents=True)
    os.environ["MPLBACKEND"]="Agg";os.environ["MPLCONFIGDIR"]=str(output/"cache/matplotlib");os.environ.setdefault("XDG_CACHE_HOME",str(output/"cache"))
    tables={name:[] for name in ("configurations","layers","sensors","terminal_fates","creator_origins","origin_to_sensor")};boots={}
    for key,group in sorted(groups.items()):
        group.sort(key=lambda task:(task.get("block",task.get("seed_block",0)),task["task_id"]))
        summary,boot,*rest=summarize_group(group,[paths[t["task_id"]] for t in group]);tables["configurations"].append(summary);boots[key]=boot
        for name,rows in zip(("layers","sensors","terminal_fates","creator_origins","origin_to_sensor"),rest):tables[name].extend(rows)
    if not args.engineering:
        contrasts=comparisons(tables["configurations"],boots)
        if contrasts:tables["comparisons"]=contrasts
    for name,rows in tables.items():write_csv(output/(name+".csv"),rows)
    plots=plot_summaries(tables["configurations"],output,args.engineering)
    report=["# 十层stack-v2分析", "", "模式："+("工程验收；不是科学证据，不能据此排序布局或选择间隙。" if args.engineering else "已接受的独立科学样本；未进行等价性判定或自动选择间隙。"),
            "", "继续保留Geant4 11.4.2默认过程注册及旧宏光学开关，包含opticalphoton上的Scintillation。初代与光学父粒子再发射分别保留。",
            "", "net为所有SiPM收集光子/入射中子；full-stack legacy_ce为全D/全局旧G，仅用于旧scan兼容性描述；same_root_legacy_ce为同root层收集/全部tile旧G。旧G不代表全部光子birth。full-stack birth_ce为tile内出生tracks自身的收集（任意目的层）/tile内全部birth；all_birth_ce另存全D/所有位置全部birth。层/传感器legacy_ce保持历史同root层D/该层旧G，并另存all_origin_legacy_ce；其birth_ce是同birth层检测/该层实际birth。",
            "", "初代(root)来源与本track实际出生(birth)来源分开；钢、世界、SiPM等已知outside来源允许并显式报告；unknown、重复、未闭合、NoRINDEX属于硬错误。",
            "", "每个bootstrap replica在每个任务块内重采完整事件，所有层、传感器与flow字段共用同一组权重。固定10,000次、seed20260929；逐项95%区间未做多重比较调整。零分母写NaN及valid=false，valid_resamples显示可用重采次数；出现无效replica时区间仅条件于有效分母。",
            "", "单事件工程smoke的重采退化，不能估计总体不确定性；其图中不展示CI。重复seed/off/旧布局兼容任务均未混入分析。",
            "", "configs/layers/sensors分别保留全部收集、同root层和同birth层关联；creator_origins/origin_to_sensor记录路径关联，不能自动解释为干预因果。不同布局的总面积不同，另外报告每sensor和每active-mm²的net。"]
    (output/"report.zh.md").write_text("\n".join(report)+"\n")
    metadata={"schema_version":"steel-stack-v2-analysis-v1","completed":True,"created_at_utc":datetime.now(timezone.utc).isoformat(),
              "engineering":args.engineering,"accepted_statistical_evidence":not args.engineering,"tasks":len(tasks),
              "events":sum(task["events"] for task in tasks),"configurations":len(groups),"bootstrap":{"resamples":REPLICAS,"seed":BOOTSTRAP_SEED,"unit":"whole event within fixed task block","interval":"pointwise percentile 95","multiplicity_adjusted":False},
              "equivalence_tested":False,"gap_selected":None,"inputs":evidence,"manifest_sha256":sha256(batch/"manifest.json"),
              "estimands":{"net":"all sensor detections / incident neutrons",
                            "legacy_ce":"all sensor detections / global legacy generated count",
                            "birth_ce":"detections of tile-born tracks at any destination / all tile-born tracks",
                            "all_birth_ce":"all sensor detections / births in all volumes",
                            "same_root_legacy_ce":"same-root-layer detections / sum of tile legacy generated counts",
                            "layer_sensor_legacy_ce":"same-root-layer detections / that layer legacy generated count",
                            "layer_sensor_all_origin_legacy_ce":"all-origin detections / that layer legacy generated count",
                            "layer_sensor_birth_ce":"same-birth-layer detections / births in that tile layer"},
              "source_hashes":{name:sha256(Path(__file__).with_name(name)) for name in ("analyze.py","audit.py","model.py")},
              "runtime":{name:importlib.metadata.version(name) for name in ("numpy","uproot","matplotlib")},"plots":plots}
    (output/"analysis.json").write_text(json.dumps(metadata,indent=2,allow_nan=False)+"\n")
    records=[{"path":str(path.relative_to(output)),"sha256":sha256(path),"bytes":path.stat().st_size}
             for path in sorted(output.iterdir()) if path.is_file()]
    write_csv(output/"MANIFEST.csv",records)
    print(json.dumps({"analysis":str(output/"analysis.json"),"engineering":args.engineering,"events":metadata["events"]}))


if __name__=="__main__":main()
