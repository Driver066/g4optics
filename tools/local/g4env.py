#!/usr/bin/env python3
"""Local Geant4 installation, version selection, and archived acceptance runs."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import time

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
APP = REPO / "test/OpNovice2"
CONFIG = json.loads((HERE / "environment.json").read_text())
ENVROOT = REPO / "outputs/environment"
ACTIVE = ENVROOT / "active.json"
IMAGE_LOCK = ENVROOT / "image-lock.json"
PYTHON = APP / ".venv-analysis/bin/python"
IMAGE = CONFIG["image"]
CONTAINER = CONFIG["container"]
CPROJECT = CONFIG["container_project_root"]
CAPP = CPROJECT + "/test/OpNovice2"
CBUILD = CAPP + "/" + CONFIG["build_dir"]


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)


def run(args, *, log=None, capture=False, env=None, cwd=REPO):
    args = [str(a) for a in args]
    if log:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        # Append repeated attempts; never truncate an earlier install/run log.
        with log.open("a") as stream:
            stream.write("\n" + dt.datetime.now(dt.timezone.utc).isoformat() + " " + shlex.join(args) + "\n")
            stream.flush()
            subprocess.run(args, cwd=cwd, env=env, stdout=stream,
                           stderr=subprocess.STDOUT, check=True)
        return ""
    result = subprocess.run(args, cwd=cwd, env=env, text=True, check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def inspect(name):
    result = subprocess.run(["docker", "container", "inspect", name],
                            capture_output=True, text=True)
    if result.returncode:
        if "No such" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)[0]


def dataset_items():
    data = json.loads((HERE / "geant4-11.4.2-datasets.json").read_text())
    return data["datasets"] if isinstance(data, dict) else data


def dataset_env():
    return {item.get("envvar", item.get("env")): "/g4data/" + item["directory"]
            for item in dataset_items()}


def dexec(args, *, container=CONTAINER, env=None, interactive=False, log=None,
          capture=False, workdir=CAPP):
    command = ["docker", "exec"]
    if interactive:
        command += ["-it"]
    command += ["-w", workdir]
    if container == CONTAINER:
        # The version manifest is authoritative even if a shell has old G4* values.
        command += ["-e", "G4DATA=/g4data"]
    command += [container, "bash", "-lc",
                'source /opt/geant4/bin/geant4.sh; ' +
                ('export ' + ' '.join(shlex.quote(k + '=' + v) for k, v in dataset_env().items()) + '; '
                 if container == CONTAINER else '') +
                ('export ' + ' '.join(shlex.quote(k + '=' + str(v)) for k, v in (env or {}).items()) + '; '
                 if env else '') + 'exec "$@"', "g4env", *map(str, args)]
    return run(command, log=log, capture=capture)


def cpath(path):
    return CPROJECT + "/" + str(Path(path).resolve().relative_to(REPO))


def batch_path(value=None):
    if value:
        batch = Path(value).resolve()
        batch.relative_to(ENVROOT.resolve())
        return batch
    batch = ENVROOT / (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-setup1142")
    batch.mkdir(parents=True, exist_ok=False)
    return batch


def backup(batch):
    before = batch / "before"
    if (before / "preserved-files.json").exists():
        return
    before.mkdir(parents=True, exist_ok=True)
    (before / "git-status.txt").write_text(run(["git", "status", "--short", "--branch"], capture=True) + "\n")
    (before / "git-head.txt").write_text(run(["git", "rev-parse", "HEAD"], capture=True) + "\n")
    save(before / "container.json", inspect(CONFIG["legacy_container"]))
    save(before / "image.json", json.loads(run(["docker", "image", "inspect", CONFIG["legacy_image_id"]], capture=True)))
    host_scripts = [Path('/Users/edison/geant4-docker/bin') / n for n in ['g4dev.sh','g4cli.sh','g4gui.sh']]
    host_scripts += [Path('/Users/edison/geant4-docker/g4docker.sh')]
    for source in host_scripts:
        dest = before / "host-scripts" / source.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(source, dest)
    rows = []
    for name in ["scan_latest", "run_config.json", "points.csv", "efficiency_map.csv", "build/OpNovice2", "build/CMakeCache.txt"]:
        p = APP / name
        row = {"path": str(p.relative_to(REPO)), "exists": p.exists(), "symlink": p.is_symlink()}
        if p.is_symlink():
            row["target"] = str(p.readlink())
        elif p.is_file():
            row["sha256"] = sha(p)
            if '/' not in name:
                shutil.copy2(p, before / name)
        rows.append(row)
    save(before / "preserved-files.json", rows)


def start_new():
    run(["docker", "info"], capture=True)
    data_root = Path(CONFIG["data_root"])
    if not data_root.is_dir():
        raise RuntimeError("Run setup first: versioned data directory is missing")
    locked = json.loads(IMAGE_LOCK.read_text())
    if locked["base_image"] != IMAGE or locked["dockerfile_sha256"] != sha(REPO / "Dockerfile"):
        raise RuntimeError("Installed image does not match the pinned build recipe; run setup")
    expected_image = run(["docker", "image", "inspect", "--format", "{{.Id}}", locked["image_id"]], capture=True)
    existing = inspect(CONTAINER)
    if existing:
        if existing["Image"] != expected_image:
            raise RuntimeError("Existing new container uses another image; refusing to replace it")
        mounts = {m["Destination"]: m for m in existing["Mounts"]}
        if mounts.get("/g4data", {}).get("Source") != str(data_root) or mounts["/g4data"].get("RW"):
            raise RuntimeError("New container has an unexpected or writable data mount")
        if mounts.get("/work", {}).get("Source") != str(REPO.parent):
            raise RuntimeError("New container has an unexpected workspace mount")
        if not existing["State"]["Running"]:
            run(["docker", "start", CONTAINER])
    else:
        run(["docker", "run", "-dit", "--name", CONTAINER, "--platform", "linux/arm64",
             "--label", "org.g4optics.environment=11.4.2", "--entrypoint", "/bin/bash",
             "--mount", "type=bind,source=" + str(REPO.parent) + ",target=/work",
             "--mount", "type=bind,source=" + str(data_root) + ",target=/g4data,readonly",
             "-e", "DISPLAY=host.docker.internal:0", "-e", "QT_X11_NO_MITSHM=1",
             "-w", "/work", expected_image])
    actual = dexec(["geant4-config", "--version"], capture=True)
    if actual != CONFIG["version"]:
        raise RuntimeError("Wrong Geant4 runtime: " + actual)


def identity(batch):
    start_new()
    version = dexec(["geant4-config", "--version"], capture=True)
    arch = dexec(["uname", "-m"], capture=True)
    features = dexec(["geant4-config", "--features"], capture=True)
    for feature in ["qt[yes]", "opengl-x11[yes]", "gdml[yes]", "multithreading[yes]"]:
        if feature not in features:
            raise RuntimeError("Missing Geant4 feature: " + feature)
    if arch != "aarch64":
        raise RuntimeError("Expected native ARM64, got " + arch)
    datasets = dexec(["geant4-config", "--check-datasets"], capture=True)
    if len(datasets.splitlines()) != 12 or any(" INSTALLED " not in line for line in datasets.splitlines()):
        raise RuntimeError("Geant4 reports missing datasets: " + datasets)
    for item in dataset_items():
        if "/g4data/" + item["directory"] not in datasets:
            raise RuntimeError("Dataset location/version mismatch: " + item["directory"])
    links = dexec(["ldd", CBUILD + "/OpNovice2"], capture=True)
    if "not found" in links or "libG4" not in links:
        raise RuntimeError("Invalid Geant4 dynamic library linkage")
    (batch / "dynamic-libraries.txt").write_text(links + "\n")
    save(batch / "runtime.json", {"passed": True, "version": version, "architecture": arch,
         "features": features, "datasets": datasets, "container": inspect(CONTAINER),
         "image": json.loads(run(["docker", "image", "inspect", inspect(CONTAINER)["Image"]], capture=True)),
         "executable_sha256": sha(APP / CONFIG["build_dir"] / "OpNovice2")})


def build_inputs():
    files = [APP / "CMakeLists.txt", APP / "OpNovice2.cc"]
    files += sorted((APP / "src").glob("*.cc")) + sorted((APP / "include").glob("*.hh"))
    return {str(path.relative_to(REPO)): sha(path) for path in files}


def acceptance_identity():
    return {"executable_sha256": sha(APP / CONFIG["build_dir"] / "OpNovice2"),
            "cmake_cache_sha256": sha(APP / CONFIG["build_dir"] / "CMakeCache.txt"),
            "build_inputs": build_inputs(),
            "image_id": inspect(CONTAINER)["Image"],
            "dataset_manifest_sha256": sha(HERE / "geant4-11.4.2-datasets.json"),
            "dataset_receipt_sha256": sha(Path(CONFIG["data_root"]) / "geant4-datasets-receipt.json")}


def build(batch):
    start_new()
    before = build_inputs()
    dexec(["cmake", "-S", ".", "-B", CONFIG["build_dir"], "-DCMAKE_BUILD_TYPE=Release",
           "-DWITH_GEANT4_UIVIS=ON", "-DGeant4_DIR=/opt/geant4/lib/cmake/Geant4"], log=batch / "build.log")
    dexec(["cmake", "--build", CONFIG["build_dir"], "-j", str(CONFIG["build_jobs"])], log=batch / "build.log")
    if before != build_inputs():
        raise RuntimeError("Build inputs changed during compilation; rebuild before acceptance")
    save(batch / "build-receipt.json", {"inputs": before,
         "executable_sha256": sha(APP / CONFIG["build_dir"] / "OpNovice2")})
    identity(batch)


def freeze(batch):
    inputs = run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], capture=True).splitlines()
    inputs = [p for p in inputs if (REPO / p).is_file() and not p.startswith("outputs/")]
    source_dir = batch / "source"
    source_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tarfile.open(source_dir / (stamp + ".tar.gz"), "w:gz") as archive:
        for p in inputs:
            archive.add(REPO / p, arcname=p, recursive=False)
    save(source_dir / (stamp + ".json"), {"git_commit": run(["git", "rev-parse", "HEAD"], capture=True),
         "git_status": run(["git", "status", "--porcelain"], capture=True),
         "files": {p: sha(REPO / p) for p in inputs},
         "executable_sha256": sha(APP / CONFIG["build_dir"] / "OpNovice2")})
    packages = run([PYTHON, "-m", "pip", "freeze", "--all"], capture=True)
    (batch / "python-freeze.txt").write_text(packages + "\n")
    versions = run([PYTHON, "-B", "-c", "import sys,importlib.metadata as m,json; print(json.dumps({'python':sys.version,'packages':{n:m.version(n) for n in ['numpy','pandas','matplotlib','uproot','awkward','Pillow']}}))"], capture=True)
    save(batch / "analysis-runtime.json", {"python": json.loads(versions),
         "root_version": run(["/opt/homebrew/bin/root-config", "--version"], capture=True),
         "root_architecture": run(["/opt/homebrew/bin/root-config", "--arch"], capture=True)})


def smoke(batch):
    if (batch / "tasks.json").exists():
        raise RuntimeError("This batch already has tasks; use a new batch for another acceptance run")
    receipt = json.loads((batch / "build-receipt.json").read_text())
    seal = acceptance_identity()
    if receipt["inputs"] != seal["build_inputs"] or receipt["executable_sha256"] != seal["executable_sha256"]:
        raise RuntimeError("Build receipt does not match current source/executable")
    if (batch / "tasks.partial.json").exists():
        raise RuntimeError("An interrupted acceptance attempt exists; use a fresh batch, preserving it")
    save(batch / "acceptance-identity.json", seal)
    tasks = []
    common = ["full", "custom", "--source-mode", "gps", "--source-model", "fixed-electron",
              "--tank-size", "100 100 5 mm", "--sipm-face", "-Z", "--sipm-local-position", "0 0 0 mm",
              "--sipm-size", "2 2 0.5 mm", "--surface-preset", "polished", "--optical-coupling", "none",
              "--primary-energy", "1 MeV", "--y-min", "0", "--y-max", "0", "--step", "5",
              "--grid-unit", "mm", "--beam-z", "4", "--events", "100", "--no-root-plots"]
    cases = [(g, x) for g in ["flat", "dimple"] for x in [0, 5, 40]] + [("flat", 0)]
    for i, (geometry, x) in enumerate(cases):
        seed_index = 0 if i == 6 else i
        seed1, seed2 = 114201 + 2 * seed_index, 114202 + 2 * seed_index
        task_id = ("repeat-" if i == 6 else "") + geometry + "-x" + str(x)
        pointer = batch / "pointers" / (task_id + ".txt")
        pointer.parent.mkdir(exist_ok=True)
        env = {"UPDATE_LATEST": "0", "SCAN_RUNS_DIR": cpath(batch / "runs"),
               "SCAN_RUN_ID_SUFFIX": task_id, "SCAN_RESULT_POINTER": cpath(pointer),
               "PLOT_WITH_ROOT": "0", "G4RUN_MANAGER_TYPE": "Serial",
               "SCAN_GIT_COMMIT": run(["git", "rev-parse", "HEAD"], capture=True),
               "SCAN_GIT_BRANCH": run(["git", "branch", "--show-current"], capture=True),
               "SCAN_GIT_DIRTY": "true" if run(["git", "status", "--porcelain"], capture=True) else "false"}
        args = common + ["--x-min", str(x), "--x-max", str(x), "--seed1", str(seed1), "--seed2", str(seed2)]
        if geometry == "dimple":
            args += ["--dimple", "--dimple-radius", "3", "--dimple-unit", "mm", "--dimple-sipm-mode", "opening"]
        env["OPNOVICE2_EXECUTABLE"] = CBUILD + "/OpNovice2"
        print("Running environment acceptance:", task_id, flush=True)
        dexec(["bash", "./run_sipm_cavity_scan.sh", *args], env=env, log=batch / "task-logs" / (task_id + ".log"))
        resolved = pointer.read_text().strip()
        run_dir = REPO / Path(resolved).relative_to(CPROJECT)
        row = {"task_id": task_id, "geometry": geometry, "x_mm": x, "y_mm": 0,
               "seed1": seed1, "seed2": seed2, "events": 100, "run_dir": str(run_dir),
               "executable_sha256": seal["executable_sha256"]}
        if i == 6:
            row["repeat_of"] = "flat-x0"
        tasks.append(row)
        save(batch / "tasks.partial.json", tasks)
    if acceptance_identity() != seal:
        raise RuntimeError("Environment changed during acceptance; this attempt cannot be accepted")
    save(batch / "tasks.json", tasks)


def setup(batch):
    batch.mkdir(parents=True, exist_ok=True)
    backup(batch)
    run(["docker", "pull", "--platform", "linux/arm64", IMAGE], log=batch / "image-pull.log")
    run(["docker", "build", "--platform", "linux/arm64", "-t", CONFIG["local_image_tag"],
         "-f", REPO / "Dockerfile", REPO], log=batch / "image-build.log")
    image_id = run(["docker", "image", "inspect", "--format", "{{.Id}}", CONFIG["local_image_tag"]], capture=True)
    locked = {"base_image": IMAGE, "image_id": image_id, "dockerfile_sha256": sha(REPO / "Dockerfile")}
    save(batch / "image-lock.json", locked)
    existing = inspect(CONTAINER)
    if existing and existing["Image"] != image_id:
        raise RuntimeError("Candidate image differs from the retained installed container; existing image lock/default were preserved")
    save(IMAGE_LOCK, locked)
    run([sys.executable, HERE / "install_datasets.py", CONFIG["data_root"],
         "--report", batch / "dataset-installation.json"], log=batch / "datasets.log")
    build(batch)
    freeze(batch)
    save(batch / "setup.json", {"completed": True, "version": "11.4.2", "default_changed": False})
    print("Installed and built. Acceptance batch:", batch)


def prepare_geometry(batch, geometry="dimple"):
    output = batch / "gui"
    output.mkdir(parents=True, exist_ok=True)
    lines = (APP / "ej200_sipm_gps_test.mac").read_text().splitlines()
    final = []
    for line in lines:
        if line.strip() == "/run/initialize":
            final += ["/opnovice2/tank/size 100 100 5 mm", "/opnovice2/tank/bottomCavity false"]
            if geometry == "dimple":
                final += ["/opnovice2/dimple/enabled true", "/opnovice2/dimple/radius 3 mm",
                          "/opnovice2/dimple/mode hemisphere", "/opnovice2/dimple/sipmMode opening"]
        if line.startswith("/analysis/setFileName"):
            line = "/analysis/setFileName " + cpath(output / (geometry + "-geometry"))
        if line.startswith("/run/beamOn"):
            line = "/run/beamOn 0"
        final.append(line)
    final += ["/geometry/test/verbosity true", "/geometry/test/resolution 10000", "/geometry/test/run",
              "/vis/open " + CONFIG["default_viewer"] + " 900x650+50+50", "/vis/drawVolume",
              "/vis/viewer/set/style surface", "/vis/viewer/set/viewpointThetaPhi 135 35 deg",
              "/vis/viewer/set/autoRefresh true", "/vis/viewer/flush"]
    macro = output / (geometry + "-default.mac")
    macro.write_text("\n".join(final) + "\n")
    return macro


def install_launchers(batch):
    destination = Path('/Users/edison/geant4-docker')
    prepared = batch / "host-launchers"
    prepared.mkdir(exist_ok=True)
    mapping = {destination / "bin/g4dev.sh": "start", destination / "bin/g4cli.sh": "shell",
               destination / "bin/g4gui.sh": "gui", destination / "g4docker.sh": "shell"}
    hashes = {}
    for path, command in mapping.items():
        text = '#!/usr/bin/env bash\nset -euo pipefail\nexec ' + shlex.quote(str(HERE / "g4env.sh")) + ' ' + command + ' "$@"\n'
        (prepared / path.name).write_text(text)
        temporary = path.with_name(path.name + ".g4env-new")
        temporary.write_text(text)
        temporary.chmod(0o755)
        temporary.replace(path)
        hashes[str(path)] = sha(path)
    save(batch / "host-launchers.json", hashes)


def write_compose_environment():
    locked = json.loads(IMAGE_LOCK.read_text())
    values = {"G4_LOCAL_IMAGE": locked["image_id"], "G4_LOCAL_CONTAINER": CONTAINER,
              "G4_LOCAL_DATA_ROOT": CONFIG["data_root"], "G4_HOST_WORKDIR": str(REPO.parent),
              "G4_LOCAL_VIEWER": CONFIG["default_viewer"]}
    (ENVROOT / "compose.env").write_text(''.join(k + '=' + v + '\n' for k, v in values.items()))


def xquartz():
    run(["open", "-a", "XQuartz"])
    for _ in range(30):
        display = run(["launchctl", "getenv", "DISPLAY"], capture=True)
        if not display and Path("/private/tmp/.X11-unix/X0").exists():
            display = ":0"
        if display:
            auth = subprocess.run(["/opt/X11/bin/xhost", "+localhost"],
                                  env={**os.environ, "DISPLAY": display}, capture_output=True, text=True)
            if auth.returncode == 0:
                return
        time.sleep(1)
    raise RuntimeError("XQuartz did not become ready; check its display and connection settings")


def selected(version=None):
    if version:
        return version
    if ACTIVE.exists():
        return json.loads(ACTIVE.read_text())["version"]
    return "11.3.2"


def check(batch, activate=False, skip_gui=False):
    identity(batch)
    seal = json.loads((batch / "acceptance-identity.json").read_text())
    if acceptance_identity() != seal:
        raise RuntimeError("Current environment differs from the tested acceptance identity; run fresh acceptance")
    run([sys.executable, HERE / "install_datasets.py", CONFIG["data_root"], "--verify-only",
         "--report", batch / "dataset-verification.json"], log=batch / "datasets-verification.log")
    command = [PYTHON, HERE / "verify_environment.py", "--batch-dir", batch]
    if skip_gui:
        command += ["--skip-gui"]
    run(command, log=batch / "verification.log")
    if activate:
        if skip_gui:
            raise RuntimeError("Cannot activate without GUI acceptance")
        restart = json.loads((batch / "restart-check.json").read_text())
        if restart.get("passed") is not True:
            raise RuntimeError("Restart/legacy acceptance is missing")
        if not json.loads((batch / "external_gui.json").read_text()).get("passed"):
            raise RuntimeError("GUI acceptance is missing")
        for name in ["external_gui.json", "restart-check.json"]:
            evidence = json.loads((batch / name).read_text())
            if evidence.get("executable_sha256") != seal["executable_sha256"] or evidence.get("image_id") != seal["image_id"]:
                raise RuntimeError("Evidence belongs to another executable/image: " + name)
        if acceptance_identity() != seal:
            raise RuntimeError("Environment changed while verification was running; not activating")
        if ACTIVE.exists():
            save(batch / "previous-active.json", json.loads(ACTIVE.read_text()))
        install_launchers(batch)
        write_compose_environment()
        save(ACTIVE, {"version": "11.4.2", "container": CONTAINER,
             "build_dir": CONFIG["build_dir"], "geant4_version": "11.4.2", "batch_dir": str(batch)})
        print("Default Geant4 environment activated: 11.4.2 (old g4dev retained)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup", "start", "shell", "gui", "scan", "check", "rollback", "status", "build", "freeze", "smoke"])
    parser.add_argument("--version", choices=["11.3.2", "11.4.2"])
    parser.add_argument("--batch-dir")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--skip-gui", action="store_true")
    argv = sys.argv[1:]
    if "--" in argv:
        split = argv.index("--")
        args = parser.parse_args(argv[:split])
        rest = argv[split + 1:]
    else:
        args, rest = parser.parse_known_args(argv)
    if rest and args.command not in ["shell", "gui", "scan"]:
        parser.error("Unexpected arguments: " + shlex.join(rest))
    version = selected(args.version)
    if args.command in ["setup", "build", "freeze", "smoke", "check"]:
        if args.version == "11.3.2":
            parser.error("Installation/acceptance commands apply to 11.4.2 only")
        batch = batch_path(args.batch_dir)
        {"setup": setup, "build": build, "freeze": freeze, "smoke": smoke,
         "check": lambda b: check(b, args.activate, args.skip_gui)}[args.command](batch)
    elif args.command == "status":
        print(json.dumps({"default_version": version, "new_container": inspect(CONTAINER),
                          "active_acceptance": json.loads(ACTIVE.read_text()) if ACTIVE.exists() else None}, indent=2))
    elif args.command == "rollback":
        legacy = inspect(CONFIG["legacy_container"])
        if not legacy:
            raise RuntimeError("Legacy container is unavailable; not changing the default")
        if legacy["Image"] != CONFIG["legacy_image_id"]:
            raise RuntimeError("Legacy container image identity changed; not changing the default")
        if ACTIVE.exists():
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            ACTIVE.rename(ENVROOT / ("active-before-rollback-" + stamp + ".json"))
        print("Default returned to retained g4dev / Geant4 11.3.2; new environment preserved")
    elif args.command == "scan":
        if version == "11.4.2":
            start_new()
        env = dict(os.environ)
        expected = {"G4_DOCKER_CONTAINER": CONTAINER if version == "11.4.2" else CONFIG["legacy_container"],
                    "G4_DOCKER_BUILD_DIR": CONFIG["build_dir"] if version == "11.4.2" else CONFIG["legacy_build_dir"],
                    "G4_EXPECTED_VERSION": version}
        for key, value in expected.items():
            if key in env and env[key] != value:
                raise RuntimeError("Conflicting managed environment override: " + key)
        env.update(expected)
        env.setdefault("ROOT_COMMAND", CONFIG["root_command"])
        for item in dataset_items():
            env.pop(item.get("envvar", item.get("env")), None)
        if version == "11.4.2":
            env.update(dataset_env())
            env["G4DATA"] = "/g4data"
            env["Geant4_DIR"] = "/opt/geant4/lib/cmake/Geant4"
        else:
            env.pop("G4DATA", None)
            env.pop("Geant4_DIR", None)
        run(["bash", APP / "run_sipm_cavity_scan_docker.sh", *rest], env=env)
    else:
        container = CONTAINER if version == "11.4.2" else CONFIG["legacy_container"]
        if version == "11.4.2":
            start_new()
        else:
            run(["docker", "start", container])
        if args.command == "gui":
            xquartz()
        if args.command != "start":
            command = rest or ["bash", "-i"]
            gui_env = {"LIBGL_ALWAYS_INDIRECT": "1", "QT_X11_NO_MITSHM": "1",
                       "G4VIS_DEFAULT_DRIVER": CONFIG["default_viewer"]} if args.command == "gui" else None
            log = None
            if args.command == "gui" and version == "11.4.2":
                batch = batch_path(args.batch_dir)
                if not rest:
                    command = [CBUILD + "/OpNovice2", "-i", cpath(prepare_geometry(batch))]
                log = batch / "gui" / ("launch-" + dt.datetime.now(dt.timezone.utc).strftime("%H%M%S") + ".log")
            dexec(command, container=container, interactive=sys.stdin.isatty(), env=gui_env, log=log)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, ValueError, OSError) as error:
        print("g4env:", error, file=sys.stderr)
        sys.exit(1)
