#!/usr/bin/env python3
"""One formal 100-event task: validate, run, audit, and stop on failure."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from precision_worker import stop_campaign, verify_sibling_states


def execute(bundle, manifest_hash):
    root = bundle.parent.parent
    stop = root/"STOP.json"
    child = None
    attempt = None
    index = 0
    def interrupted(signum, frame):
        raise RuntimeError("Formal task termination/timeout signal: "+str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        from model import canonical_json
        from osc import read, require, sha256
        from precision_io import save_new
        from science_io import read_campaign, check_bundle, audit_execution
        require(not stop.exists(), "Formal campaign stopped by earlier failure")
        require(sha256(bundle/"manifest.json") == manifest_hash, "Formal bundle manifest changed")
        m = read(bundle/"manifest.json")
        c = m["science_control"]
        require(Path(c["campaign_root"]).resolve() == root, "Formal worker outside campaign")
        campaign,tasks = read_campaign(root,c["campaign_sha256"])
        info = next(x for x in campaign["bundles"] if Path(x["path"]).resolve() == bundle)
        check_bundle(campaign,tasks,info,c["campaign_sha256"])
        index = int(os.environ.get("SLURM_ARRAY_TASK_ID","0"))
        job = os.environ.get("SLURM_ARRAY_JOB_ID","")
        require(job.isdigit() and os.environ.get("SLURM_CPUS_PER_TASK") == "1" and
                1 <= index <= info["tasks"], "Formal scheduler index/CPU identity invalid")
        submission = read(root/"submissions"/(info["name"]+".json"))
        require(submission["job_id"] == job and submission["campaign_sha256"] == c["campaign_sha256"],
                "Array is not the recorded formal submission")
        task = tasks[info["offset"]+index-1]
        jobs = [read(p)["job_id"] for p in sorted((root/"submissions").glob("array-???.json"))]
        gate = subprocess.run(["sacct","-j",",".join(jobs),"-X","--noheader","--parsable2",
                               "--format=JobID%40,State%40,ExitCode"],text=True,stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,check=True,timeout=30)
        gates = root/"scheduler-gates"
        gates.mkdir(exist_ok=True)
        save_new(gates/(job+"_"+str(index)+".json"),dict(stdout=gate.stdout,stderr=gate.stderr))
        verify_sibling_states(gate.stdout)
        attempt = root/"results"/task["task_id"]/("job-"+job+"-task-"+str(index))
        require(not attempt.exists() and not stop.exists(), "Preserve existing attempt or respect STOP")
        child = subprocess.Popen([sys.executable,str(bundle/"array_task.py"),str(bundle/"manifest.json"),manifest_hash],
                                 start_new_session=True)
        code = child.wait(timeout=3540)
        require(code == 0, "Formal simulation worker failed: "+str(code))
        result = audit_execution(campaign,m,task,index,attempt/"execution.json",manifest_hash)
        save_new(attempt/"science-audit.json",result)
        save_new(attempt/"accepted.json",dict(accepted_for_main=True,accepted_for_calibration=False,
            task_id=task["task_id"],events=task["events"],campaign_sha256=c["campaign_sha256"],
            manifest_sha256=manifest_hash,registered_task_sha256=hashlib.sha256(canonical_json(task)).hexdigest(),
            audit_sha256=sha256(attempt/"science-audit.json"),execution_sha256=sha256(attempt/"execution.json")))
        print("FORMAL ACCEPTED",task["task_id"],task["events"],flush=True)
        return 0
    except BaseException as error:
        stop_campaign(stop,error,index)
        if child is not None and child.poll() is None:
            os.killpg(child.pid,signal.SIGTERM)
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL)
                child.wait()
        if attempt is not None and attempt.exists() and not(attempt/"failure.json").exists():
            with (attempt/"failure.json").open("x") as stream:
                json.dump(dict(error=str(error),index=index),stream)
        print("FORMAL STOPPED:",str(error),file=sys.stderr,flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(execute(Path(sys.argv[1]).resolve().parent,sys.argv[2]))
