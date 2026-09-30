#!/usr/bin/env python3
"""One calibration array element: run, audit, or stop the remaining campaign."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def stop_campaign(path, error, index):
    try:
        with path.open("x") as stream:
            json.dump(dict(error=str(error), array_index=index,
                           scheduler_job=os.environ.get("SLURM_ARRAY_JOB_ID"),
                           created_epoch=time.time()), stream, indent=2)
    except FileExistsError:
        pass  # Preserve the first failure, not whichever sibling stopped last.


def verify_sibling_states(text):
    failures = []
    for line in text.splitlines():
        fields = line.split("|")
        if len(fields) < 3:
            raise ValueError("Malformed scheduler gate response")
        state = fields[1].split()[0].rstrip("+")
        if state not in ("PENDING", "RUNNING", "COMPLETING", "COMPLETED", "CONFIGURING", "SUSPENDED"):
            failures.append(line)
        if state == "COMPLETED" and fields[2] != "0:0":
            failures.append(line)
    if failures:
        raise ValueError("Earlier scheduler failure in this calibration array: "+str(failures))


def execute(bundle, manifest_hash):
    # This location is fixed by prepare; failures during imports must also stop.
    stop = bundle.parent/"STOP.json"
    child = None
    index = 0
    attempt = None
    def interrupted(signum, frame):
        raise RuntimeError("Scheduler termination or task timeout signal: "+str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        index = int(os.environ.get("SLURM_ARRAY_TASK_ID", "0"))
        from model import canonical_json
        from osc import require, sha256
        from precision_io import read_campaign, audit_execution, save_new
        require(not stop.exists(), "Calibration stopped by earlier failure")
        m, tasks = read_campaign(bundle, manifest_hash)
        require(os.environ.get("SLURM_CPUS_PER_TASK") == "1" and os.environ.get("SLURM_JOB_ID"),
                "Calibration requires one allocated CPU")
        require(1 <= index <= len(tasks), "Invalid array task index")
        task = tasks[index-1]
        job = os.environ["SLURM_ARRAY_JOB_ID"]
        require(job.isdigit(), "Invalid scheduler job ID")
        # Also catch failures unable to write STOP, such as a node or OOM kill.
        gate = subprocess.run(["sacct", "-j", job, "-X", "--noheader", "--parsable2",
                               "--format=JobID,State,ExitCode"], text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True)
        gates = bundle.parent/"scheduler-gates"
        gates.mkdir(exist_ok=True)
        save_new(gates/(job+"_"+str(index)+".json"), dict(stdout=gate.stdout, stderr=gate.stderr))
        verify_sibling_states(gate.stdout)
        attempt = Path(m["remote_output_root"])/task["task_id"]/("job-"+job+"-task-"+str(index))
        require(not attempt.exists(), "Preserve the previous attempt")
        # The inner frozen worker creates the attempt and records raw execution.
        require(not stop.exists(), "Calibration stopped during preflight")
        child = subprocess.Popen([sys.executable, str(bundle/"array_task.py"),
                                  str(bundle/"manifest.json"), manifest_hash], start_new_session=True)
        code = child.wait(timeout=3540)
        require(code == 0, "Simulation worker failed with exit "+str(code))
        result = audit_execution(m, task, index, attempt/"execution.json", manifest_hash)
        save_new(attempt/"calibration-audit.json", result)
        save_new(attempt/"accepted.json", dict(accepted_for_calibration=True, accepted_for_main=False,
            task_id=task["task_id"], events=task["events"], manifest_sha256=manifest_hash,
            registered_task_sha256=hashlib.sha256(canonical_json(task)).hexdigest(),
            audit_sha256=sha256(attempt/"calibration-audit.json"),
            execution_sha256=sha256(attempt/"execution.json")))
        print("ACCEPTED", task["task_id"], task["events"], flush=True)
        return 0
    except BaseException as error:
        stop_campaign(stop, error, index)
        if child is not None and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        if attempt is not None and attempt.exists() and not (attempt/"failure.json").exists():
            with (attempt/"failure.json").open("x") as stream:
                json.dump(dict(error=str(error), index=index), stream)
        print("CALIBRATION STOPPED:", str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(execute(Path(sys.argv[1]).resolve().parent, sys.argv[2]))
