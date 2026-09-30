#!/usr/bin/env python3
"""Offline stack-v2 OSC validation/rendering; never connect or submit a job.

A build receipt must describe a separately built Linux x86_64 executable,
its exact frozen source manifest, Geant4 11.4.2 SIF, and complete dataset
receipt. Artifacts are read locally for validation. Remote paths only describe
where a human will stage those same artifacts. Rendering is not submission or
scientific acceptance. Mock receipts require --allow-mock and cannot execute.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import struct
import tempfile

from model import make_matrix, make_configuration, prepare_tasks, validate_tasks

SCHEMA = "steel-stack-v2-osc-render-v1"
BUILD_SCHEMA = "steel-stack-v2-osc-build-v1"
VERSION = "11.4.2"
HASH = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_MODEL = "tools/steel_stack_v2/model.py"
SOURCE_RUNNER = "test/OpNovice2/run_sipm_cavity_scan.sh"
SOURCE_DATASETS = "tools/local/geant4-11.4.2-datasets.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def remote_path(value, label):
    require(isinstance(value, str) and value.startswith("/") and value != "/",
            f"{label} must be an explicit absolute remote path")
    require(re.fullmatch(r"/[A-Za-z0-9_./-]+", value) is not None and
            ".." not in PurePosixPath(value).parts, f"Unsafe {label}")
    return str(PurePosixPath(value))


def positive_integer(value, label):
    require(isinstance(value, int) and not isinstance(value, bool) and value > 0,
            f"{label} must be an explicit positive integer")
    return value


def artifact(value, base, label):
    require(isinstance(value, dict) and isinstance(value.get("path"), str), f"Missing {label} artifact")
    require(isinstance(value.get("sha256"), str) and HASH.fullmatch(value["sha256"]), f"Missing {label} SHA256")
    path = Path(value["path"]).expanduser()
    if not path.is_absolute():
        path = base / path
    require(path.is_file() and not path.is_symlink(), f"Missing regular {label} file: {path}")
    require(sha256(path) == value["sha256"], f"{label} SHA256 mismatch")
    return path.resolve()


def require_x86_elf(path):
    with Path(path).open("rb") as stream:
        header = stream.read(64)
    require(len(header) == 64 and header[:4] == b"\x7fELF" and header[4:7] == b"\x02\x01\x01",
            "Executable must be a Linux ELF64 little-endian file, not a local Mach-O/ARM binary")
    require(struct.unpack_from("<H", header, 18)[0] == 62,
            "Executable is not EM_X86_64; local aarch64 binaries cannot be used on OSC")
    require(struct.unpack_from("<H", header, 16)[0] in (2, 3), "ELF must be executable or PIE")
    require(Path(path).stat().st_mode & 0o111, "Frozen executable lacks execute permission")


def validate_source(manifest_path, source_root=None):
    manifest_path = Path(manifest_path).resolve()
    document = read(manifest_path)
    require(document.get("schema_version") == "steel-stack-v2-frozen-source-v1",
            "Use the candidate frozen-source manifest, not historical reference or live checkout metadata")
    expected = document.get("source_files")
    require(isinstance(expected, dict) and expected, "Frozen source inventory is empty")
    for name, digest in expected.items():
        pure = PurePosixPath(name)
        require(not pure.is_absolute() and ".." not in pure.parts and str(pure) == name,
                "Unsafe frozen source path")
        require(isinstance(digest, str) and HASH.fullmatch(digest), "Invalid frozen source SHA256")
    require({SOURCE_MODEL, SOURCE_RUNNER, SOURCE_DATASETS} <= set(expected),
            "Frozen source lacks the v2 model, runner, or pinned dataset catalog")
    source_root = Path(source_root).resolve() if source_root else manifest_path.parent / "source"
    require(source_root.is_dir(), "Frozen source directory is missing")
    actual = {}
    for path in source_root.rglob("*"):
        require(not path.is_symlink(), "Frozen source must not contain symlinks")
        if path.is_file() and "__pycache__" not in path.parts:
            actual[path.relative_to(source_root).as_posix()] = sha256(path)
    require(actual == expected, "Frozen source file inventory/hash mismatch")
    require(sha256(Path(__file__).with_name("model.py")) == expected[SOURCE_MODEL],
            "Rendering model.py differs from the frozen model; use the matching frozen tool")
    return document, source_root


def validate_inputs(source_manifest, build_receipt, *, matrix, gaps, events_per_task,
                    blocks, campaign_seed, total_event_budget, source_root=None,
                    allow_mock=False, optical_numerics="legacy", corner_scale=0,
                    purpose="science", excluded_seeds=()):
    source_manifest = Path(source_manifest).resolve()
    build_receipt = Path(build_receipt).resolve()
    source, source_root = validate_source(source_manifest, source_root)
    build = read(build_receipt)
    require(build.get("schema_version") == BUILD_SCHEMA, "Unsupported OSC build receipt")
    require(build.get("os") == "linux" and build.get("architecture") == "x86_64",
            "Remote build receipt must declare linux/x86_64")
    require(build.get("geant4_version") == VERSION and build.get("run_manager") == "Serial",
            "OSC v2 requires Geant4 11.4.2 and Serial")
    source_hash = sha256(source_manifest)
    require(build.get("source_manifest_sha256") == source_hash, "Remote executable belongs to another frozen source")
    require(isinstance(build.get("mock"), bool), "Build receipt must explicitly label mock true or false")
    require(not build["mock"] or allow_mock, "Mock build receipt requires --allow-mock")
    binary = artifact(build.get("executable"), build_receipt.parent, "executable")
    require_x86_elf(binary)
    sif = artifact(build.get("sif"), build_receipt.parent, "SIF")
    require(build["sif"].get("architecture") == "x86_64" and build["sif"].get("geant4_version") == VERSION,
            "SIF must explicitly identify x86_64 and Geant4 11.4.2")
    data_receipt = artifact(build.get("dataset_receipt"), build_receipt.parent, "dataset receipt")
    data = read(data_receipt)
    catalog_path = source_root / SOURCE_DATASETS
    catalog = read(catalog_path)
    require(catalog.get("geant4_version") == VERSION and len(catalog.get("datasets", [])) == 12,
            "Frozen dataset catalog is not the complete Geant4 11.4.2 catalog")
    require(data.get("schema_version") == 1 and data.get("complete") is True and
            data.get("geant4_version") == VERSION and data.get("manifest_sha256") == sha256(catalog_path),
            "Dataset receipt is incomplete or belongs to another version/catalog")
    data_entries = data.get("datasets", [])
    require(len(data_entries) == 12, "Dataset receipt must contain exactly 12 packages")
    by_name = {entry["name"]: entry for entry in data_entries}
    require(len(by_name) == 12, "Duplicate dataset identity")
    for expected in catalog["datasets"]:
        observed = by_name.get(expected["name"], {})
        require(all(observed.get(k) == v for k, v in expected.items()), "Dataset package/version identity mismatch")
        require(HASH.fullmatch(str(observed.get("receipt_sha256", ""))) and
                HASH.fullmatch(str(observed.get("archive_sha256", ""))) and observed.get("file_count", 0) > 0,
                "Dataset package integrity receipt is missing")
    paths = {
        "source_root": remote_path(build.get("source_root_remote"), "source_root_remote"),
        "executable": remote_path(build["executable"].get("remote_path"), "executable.remote_path"),
        "sif": remote_path(build["sif"].get("remote_path"), "sif.remote_path"),
        "data_root": remote_path(build.get("data_root_remote"), "data_root_remote"),
    }
    # The source tree is immutable; binaries, data, and all outputs live outside it.
    source_remote = PurePosixPath(paths["source_root"])
    for key in ("executable", "sif", "data_root"):
        require(source_remote not in PurePosixPath(paths[key]).parents and paths[key] != paths["source_root"],
                "Remote artifacts must be staged outside the frozen source tree")
    require(purpose in ("science", "benchmark", "calibration"), "Purpose must be science, benchmark or calibration")
    configs = [make_configuration(c["layout"], c["tile_thickness_mm"], c["gap_mm"],
                                  optical_numerics, corner_scale) for c in make_matrix(matrix, gaps)]
    tasks = prepare_tasks(configs, events_per_task=events_per_task, blocks=blocks,
                          campaign_seed=campaign_seed, total_event_budget=total_event_budget, stage=purpose,
                          excluded_seeds=excluded_seeds)
    for task in tasks:
        task["purpose"] = {"benchmark":"engineering-benchmark-not-scientific-evidence",
                           "calibration":"sample-size-calibration-only", "science":"science"}[purpose]
    summary = validate_tasks(tasks, total_event_budget=total_event_budget)
    return {
        "schema_version": SCHEMA, "state": "validated-not-submitted", "submitted": False,
        "remote_runtime_verified": False, "mock": build["mock"], "geant4_version": VERSION,
        "architecture": "x86_64", "run_manager": "Serial", "matrix": matrix,
        "purpose": purpose, "optical_numerics": {"profile": optical_numerics, "scale": corner_scale},
        "gap_values_mm": sorted(float(g) for g in gaps), "events_per_task": events_per_task,
        "blocks": blocks, "campaign_seed": campaign_seed, "total_event_budget": total_event_budget,
        "summary": summary, "tasks": tasks, "source_files": source["source_files"],
        "source_manifest_sha256": source_hash, "build_receipt_sha256": sha256(build_receipt),
        "executable_sha256": sha256(binary), "sif_sha256": sha256(sif),
        "dataset_receipt_sha256": sha256(data_receipt), "datasets": catalog["datasets"],
        "dataset_package_receipts": {e["directory"]: e["receipt_sha256"] for e in data_entries},
        "git_commit": source.get("git_commit", "unknown"), "git_dirty": bool(source.get("git_status")), "remote_paths": paths,
        "local_staging_inputs": {"source_root": str(source_root), "executable": str(binary),
                                 "sif": str(sif), "dataset_receipt": str(data_receipt)},
    }


def scan_args(task):
    config = task["config"]
    numerics=config.get("optical_numerics", {"profile":"legacy","scale":0})
    args = ["full", "custom", "--study-preset", "steel-module-stack-v2",
            "--sipm-layout", config["layout"], "--tile-thickness-mm", format(config["tile_thickness_mm"], ".12g"),
            "--readout-gap-mm", format(config["gap_mm"], ".12g"),
            "--stack-photon-accounting", "on", "--events", str(task["events"]),
            "--seed1", str(task["seed1"]), "--seed2", str(task["seed2"]),
            "--x-min", "0", "--x-max", "0", "--y-min", "0", "--y-max", "0",
            "--step", "1", "--grid-unit", "mm", "--no-root-plots"]
    args += ["--optical-numerics", numerics["profile"]]
    if numerics["scale"]: args += ["--optical-corner-scale",str(numerics["scale"])]
    return args


# Standalone rendered worker. It only runs when a human later submits the
# generated ordinary array; neither validate nor render imports/runs this code.
WORKER = r'''#!/usr/bin/env python3
"""One manually scheduled array element; never submits or resubmits a job."""
import hashlib, json, os, platform, re, resource, shlex, struct, subprocess, sys, time
from pathlib import Path

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def check(condition, message):
    if not condition: raise RuntimeError(message)

manifest_path = Path(sys.argv[1])
check(digest(manifest_path) == sys.argv[2], 'Rendered manifest changed')
m = json.loads(manifest_path.read_text())
check(not m['mock'], 'Mock render cannot execute a scientific task')
check(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'Wrong compute architecture')
check(os.environ.get('SLURM_CPUS_PER_TASK') == '1', 'This array requires one CPU per task')
check(digest(Path(__file__)) == m['worker_sha256'], 'Rendered worker changed')
tasks_path = manifest_path.with_name('tasks.json')
check(digest(tasks_path) == m['tasks_sha256'], 'Task registry changed')
tasks = json.loads(tasks_path.read_text())
index = int(os.environ['SLURM_ARRAY_TASK_ID'])
check(1 <= index <= len(tasks), 'Invalid array index')
task = tasks[index-1]
p = m['remote_paths']

def check_stop():
    if m['purpose'] == 'calibration':
        check(not Path(m['calibration_control']['stop_file']).exists(), 'Calibration stopped by an earlier failure')
    if m['purpose'] == 'science' and 'science_control' in m:
        check(not Path(m['science_control']['stop_file']).exists(), 'Formal campaign stopped by an earlier failure')

def verify_inputs():
    check(digest(p['executable']) == m['executable_sha256'], 'Executable changed')
    with Path(p['executable']).open('rb') as stream: header = stream.read(64)
    check(len(header) == 64 and header[:7] == b'\x7fELF\x02\x01\x01' and
          struct.unpack_from('<H', header, 18)[0] == 62, 'Executable is not ELF64 x86_64')
    check(digest(p['sif']) == m['sif_sha256'], 'SIF changed')
    root = Path(p['source_root'])
    actual = {}
    for f in root.rglob('*'):
        check(not f.is_symlink(), 'Symlink in frozen source')
        if f.is_file() and '__pycache__' not in f.parts: actual[f.relative_to(root).as_posix()] = digest(f)
    check(actual == m['source_files'], 'Frozen source changed')
    data = Path(p['data_root'])
    check(digest(data/'geant4-datasets-receipt.json') == m['dataset_receipt_sha256'], 'Dataset version receipt changed')
    for directory, expected in m['dataset_package_receipts'].items():
        check(digest(data/directory/'.geant4-dataset-receipt.json') == expected, 'Dataset package receipt changed')

check_stop()
verify_inputs()
job = os.environ['SLURM_ARRAY_JOB_ID']
check(re.fullmatch(r'[0-9]+', job) is not None, 'Invalid array job identity')
task_dir = Path(m['remote_output_root']) / task['task_id'] / ('job-'+job+'-task-'+str(index))
task_dir.mkdir(parents=True, exist_ok=False)
receipt = dict(schema_version='steel-stack-v2-osc-execution-v1', task_id=task['task_id'],
              manifest_sha256=sys.argv[2], events=task['events'], accepted=False,
              executable_sha256=m['executable_sha256'], source_manifest_sha256=m['source_manifest_sha256'],
              sif_sha256=m['sif_sha256'], dataset_receipt_sha256=m['dataset_receipt_sha256'],
              status='running', scheduler_job=job, array_index=index,
              global_task_index=m.get('task_offset',0)+index,
              purpose=task.get('purpose','science'), optical_numerics=task['config']['optical_numerics'],
              hostname=platform.node())
receipt_path = task_dir/'execution.json'

def record():
    temporary = receipt_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(receipt, indent=2)+'\n')
    temporary.replace(receipt_path)

record()
dataset_env = {e['env']: '/g4data/'+e['directory'] for e in m['datasets']}
environment = dict(dataset_env, G4DATA='/g4data', G4RUN_MANAGER_TYPE='Serial',
                   OMP_NUM_THREADS='1', OPNOVICE2_EXECUTABLE='/opt/frozen/'+Path(p['executable']).name,
                   UPDATE_LATEST='0', PLOT_WITH_ROOT='0', PYTHONDONTWRITEBYTECODE='1', DISPLAY='',
                   SCAN_RUNS_DIR='/outputs/runs', SCAN_RESULT_POINTER='/outputs/result.txt',
                   SCAN_RUN_ID_SUFFIX=task['task_id'], SCAN_GIT_COMMIT=m['git_commit'],
                   SCAN_GIT_BRANCH='frozen-stack-v2-osc-source', SCAN_GIT_DIRTY='true' if m['git_dirty'] else 'false')
shell = ('set -euo pipefail; source /opt/geant4/bin/geant4.sh; '
         'test "$(geant4-config --version)" = 11.4.2; test "$(uname -m)" = x86_64; export ' +
         ' '.join(shlex.quote(k+'='+v) for k,v in environment.items()) +
         '; cd /work/g4optics/test/OpNovice2; exec bash ./run_sipm_cavity_scan.sh "$@"')
command = ['apptainer','exec','--cleanenv',
           '--bind',p['source_root']+':/work/g4optics:ro',
           '--bind',p['data_root']+':/g4data:ro',
           '--bind',str(Path(p['executable']).parent)+':/opt/frozen:ro',
           '--bind',str(task_dir)+':/outputs', p['sif'], 'bash','-c',shell,'stack-v2',*task['scan_args']]
started = time.monotonic()
usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
try:
    check_stop()
    with (task_dir/'launcher.log').open('x') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    receipt.update(launcher_elapsed_seconds=time.monotonic()-started,
                   child_user_seconds=usage_after.ru_utime-usage_before.ru_utime,
                   child_system_seconds=usage_after.ru_stime-usage_before.ru_stime,
                   child_max_rss_kib=usage_after.ru_maxrss)
    receipt['exit_code'] = result.returncode
    check(result.returncode == 0, 'Simulation launcher failed')
    verify_inputs()
    pointer = (task_dir/'result.txt').read_text().strip()
    relative = Path(pointer).relative_to('/outputs')
    run_dir = (task_dir/relative).resolve()
    run_dir.relative_to(task_dir.resolve())
    receipt.update(status='execution-complete-not-audited', run_dir=str(run_dir),
                   artifacts={f.relative_to(run_dir).as_posix():digest(f) for f in run_dir.rglob('*') if f.is_file()})
except Exception as error:
    receipt.update(status='failed',error=str(error))
    raise
finally:
    receipt['elapsed_seconds'] = time.monotonic()-started
    record()
'''


def render(plan, output_dir, *, remote_bundle_root, remote_output_root, account,
           time_minutes, memory_gib, max_parallel, node_constraint=None):
    output_dir = Path(output_dir).expanduser().resolve()
    require(not output_dir.exists(), "Render output already exists; refusing to overwrite an earlier plan")
    frozen_source = Path(plan["local_staging_inputs"]["source_root"]).resolve()
    require(output_dir != frozen_source and frozen_source not in output_dir.parents,
            "Render output must not modify the local frozen source tree")
    bundle = remote_path(remote_bundle_root, "remote bundle root")
    output = remote_path(remote_output_root, "remote output root")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", account or ""), "Explicit OSC account is required")
    for value, label in ((time_minutes, "time_minutes"), (memory_gib, "memory_gib"), (max_parallel, "max_parallel")):
        positive_integer(value, label)
    require(node_constraint in (None, "40core", "48core"), "Unsupported Pitzer node constraint")
    if plan["purpose"] == "calibration":
        require("calibration_control" in plan, "Calibration rendering requires precision.py prepare and task audits")
        require((time_minutes, memory_gib, max_parallel, node_constraint) == (60, 4, 4, "40core"),
                "Calibration resources differ from the registered plan")
    for path in (bundle, output):
        for protected in (plan["remote_paths"]["source_root"], plan["remote_paths"]["data_root"]):
            require(path != protected and PurePosixPath(protected) not in PurePosixPath(path).parents,
                    "Rendered bundle/output must be outside frozen source and data roots")
    require(bundle != output, "Bundle and results directories must be distinct")
    tasks = [{**task, "scan_args": scan_args(task)} for task in plan["tasks"]]
    manifest = {key: value for key, value in plan.items() if key != "tasks"}
    manifest.update(state="rendered-not-submitted", remote_bundle_root=bundle, remote_output_root=output,
                    scheduler={"kind":"ordinary-slurm-array", "account":account, "nodes":1, "ntasks":1,
                               "cpus_per_task":1, "time_minutes":time_minutes, "memory_gib":memory_gib,
                               "node_constraint":node_constraint,
                               "max_parallel":min(max_parallel,len(tasks)),
                               "maximum_requested_cpu_hours":len(tasks)*time_minutes/60})
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="."+output_dir.name+"-", dir=output_dir.parent))
    try:
        write(stage/"tasks.json", tasks)
        (stage/"array_task.py").write_text(WORKER, encoding="utf-8")
        manifest["tasks_sha256"] = sha256(stage/"tasks.json")
        manifest["worker_sha256"] = sha256(stage/"array_task.py")
        write(stage/"manifest.json", manifest)
        h, minute = divmod(time_minutes, 60)
        constraint_line = f"#SBATCH --constraint={node_constraint}\n" if node_constraint else ""
        launch = f"exec python3 {shlex.quote(bundle+'/array_task.py')} {shlex.quote(bundle+'/manifest.json')} {sha256(stage/'manifest.json')}"
        preamble = ""
        if plan["purpose"] == "calibration" or "science_control" in plan:
            control = plan["calibration_control"] if plan["purpose"] == "calibration" else plan["science_control"]
            wrapper = "precision_worker.py" if plan["purpose"] == "calibration" else "science_worker.py"
            preamble = "#SBATCH --no-requeue\n#SBATCH --signal=B:TERM@60\n"
            launch = ("module load python/3.12\nexport PYTHONNOUSERSITE=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1\nexec "
                      +shlex.join([control["python"], control["root"]+"/"+wrapper,
                                   bundle+"/manifest.json", sha256(stage/"manifest.json")]))
        sbatch = f'''#!/usr/bin/env bash
# Rendered only. No job has been submitted; inspect the frozen inputs first.
#SBATCH --job-name=g4-stack-v2
#SBATCH --account={account}
#SBATCH --time={h:02d}:{minute:02d}:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem={memory_gib}G
{constraint_line}#SBATCH --array=1-{len(tasks)}%{min(max_parallel,len(tasks))}
#SBATCH --output={bundle}/slurm-%x-%A_%a.out
{preamble}set -euo pipefail
export G4RUN_MANAGER_TYPE=Serial OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
{launch}
'''
        (stage/"array.sbatch").write_text(sbatch, encoding="utf-8")
        (stage/"scan-args.txt").write_text("\n".join(shlex.join(t["scan_args"]) for t in tasks)+"\n", encoding="utf-8")
        (stage/"SHA256SUMS").write_text("\n".join(f"{sha256(p)}  {p.name}" for p in sorted(stage.iterdir()) if p.is_file())+"\n")
        os.rename(stage, output_dir)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "render"))
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--build-receipt", required=True, type=Path)
    parser.add_argument("--matrix", required=True, choices=("sensitivity", "full"))
    parser.add_argument("--gap-mm", required=True, action="append", type=float)
    parser.add_argument("--events-per-task", required=True, type=int)
    parser.add_argument("--blocks", required=True, type=int)
    parser.add_argument("--campaign-seed", required=True, type=int)
    parser.add_argument("--total-event-budget", required=True, type=int)
    parser.add_argument("--allow-mock", action="store_true")
    parser.add_argument("--optical-numerics", choices=("legacy", "painted-corner-v1", "painted-corner-v2"), default="legacy")
    parser.add_argument("--optical-corner-scale", type=int, default=0)
    parser.add_argument("--purpose", choices=("science", "benchmark", "calibration"), default="science")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--remote-bundle-root")
    parser.add_argument("--remote-output-root")
    parser.add_argument("--account")
    parser.add_argument("--time-minutes", type=int)
    parser.add_argument("--memory-gib", type=int)
    parser.add_argument("--max-parallel", type=int)
    parser.add_argument("--node-constraint", choices=("40core", "48core"))
    args = parser.parse_args(argv)
    if args.command == "render":
        for name in ("output_dir", "remote_bundle_root", "remote_output_root", "account", "time_minutes", "memory_gib", "max_parallel"):
            if getattr(args, name) is None:
                parser.error("render requires explicit --"+name.replace("_", "-"))
    plan = validate_inputs(args.source_manifest, args.build_receipt, matrix=args.matrix, gaps=args.gap_mm,
                           events_per_task=args.events_per_task, blocks=args.blocks, campaign_seed=args.campaign_seed,
                           total_event_budget=args.total_event_budget, source_root=args.source_root, allow_mock=args.allow_mock,
                           optical_numerics=args.optical_numerics, corner_scale=args.optical_corner_scale, purpose=args.purpose)
    if args.command == "render":
        plan = render(plan, args.output_dir, remote_bundle_root=args.remote_bundle_root,
                      remote_output_root=args.remote_output_root, account=args.account, time_minutes=args.time_minutes,
                      memory_gib=args.memory_gib, max_parallel=args.max_parallel,
                      node_constraint=args.node_constraint)
    print(json.dumps({key:plan[key] for key in ("state", "submitted", "remote_runtime_verified", "mock", "summary")}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise SystemExit("OSC preparation rejected: "+str(error))
