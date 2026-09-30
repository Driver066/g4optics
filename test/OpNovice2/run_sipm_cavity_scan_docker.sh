#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

CONTAINER_WORKDIR="${G4_DOCKER_WORKDIR:-/work/g4optics/test/OpNovice2}"
CONTAINER_PROJECT_ROOT="${G4_DOCKER_PROJECT_ROOT:-/work/g4optics}"
HOST_PROJECT_ROOT="${G4_HOST_PROJECT_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd -P)}"
ACTIVE_CONTAINER=""
ACTIVE_BUILD_DIR=""
ACTIVE_EXPECTED_VERSION=""
ACTIVE_ENVIRONMENT_FILE="${HOST_PROJECT_ROOT}/outputs/environment/active.json"
if [[ -f "${ACTIVE_ENVIRONMENT_FILE}" ]]; then
  ACTIVE_DEFAULTS="$(python3 -B - "${ACTIVE_ENVIRONMENT_FILE}" <<'PY'
import json
from pathlib import Path
import sys
configuration = json.loads(Path(sys.argv[1]).read_text())
for key in ('container', 'build_dir', 'geant4_version'):
    value = configuration.get(key)
    if not isinstance(value, str) or not value or '\n' in value or '\r' in value:
        raise SystemExit(f"Invalid active environment {key}: {sys.argv[1]}")
    print(value)
PY
  )"
  {
    IFS= read -r ACTIVE_CONTAINER
    IFS= read -r ACTIVE_BUILD_DIR
    IFS= read -r ACTIVE_EXPECTED_VERSION
  } <<< "${ACTIVE_DEFAULTS}"
  if [[ -n "${G4_DOCKER_CONTAINER:-}" && "${G4_DOCKER_CONTAINER}" != "${ACTIVE_CONTAINER}" ]]; then
    ACTIVE_CONTAINER=""
    ACTIVE_BUILD_DIR=""
    ACTIVE_EXPECTED_VERSION=""
  fi
fi
CONTAINER="${G4_DOCKER_CONTAINER:-${ACTIVE_CONTAINER:-g4dev}}"
CONTAINER_BUILD_DIR="${G4_DOCKER_BUILD_DIR:-${ACTIVE_BUILD_DIR:-build}}"
EXPECTED_VERSION_VALUE="${G4_EXPECTED_VERSION-${ACTIVE_EXPECTED_VERSION}}"
CONTAINER_BUILD_JOBS="${G4_DOCKER_BUILD_JOBS:-4}"
FORCE_REBUILD_VALUE="${G4_FORCE_REBUILD:-0}"

N_EVENTS_VALUE="${N_EVENTS:-100}"
DRY_RUN_VALUE="${DRY_RUN:-0}"
SOURCE_MODE_VALUE="${SOURCE_MODE:-auto}"
PLOT_WITH_ROOT_VALUE="${PLOT_WITH_ROOT:-1}"
ROOT_COMMAND_VALUE="${ROOT_COMMAND:-root}"
ROOT_PLOT_FIDUCIAL_LIMIT_MM_VALUE="${ROOT_PLOT_FIDUCIAL_LIMIT_MM:-45}"
RUN_MANAGER_TYPE_VALUE="${G4RUN_MANAGER_TYPE:-Serial}"
USER_ARGS=("$@")

HOST_GIT_COMMIT="${SCAN_GIT_COMMIT:-$(git rev-parse HEAD 2>/dev/null || echo "unknown")}"
HOST_GIT_BRANCH="${SCAN_GIT_BRANCH:-$(git branch --show-current 2>/dev/null || echo "unknown")}"
if [[ -z "${HOST_GIT_BRANCH}" ]]; then
  HOST_GIT_BRANCH="detached"
fi
if [[ -n "${SCAN_GIT_DIRTY:-}" ]]; then
  HOST_GIT_DIRTY="${SCAN_GIT_DIRTY}"
elif [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
  HOST_GIT_DIRTY="true"
else
  HOST_GIT_DIRTY="false"
fi

usage() {
  cat <<'USAGE'
Usage:
  ./run_sipm_cavity_scan_docker.sh [run_sipm_cavity_scan.sh args...]

This host-side wrapper runs the Geant4 scan inside the g4dev Docker container,
then generates ROOT macro plots on the host from this invocation's exact run.
All scan options are passed through, including --surface-preset and --dimple.
The build is refreshed on every invocation, including --dry-run.

Environment:
  G4_DOCKER_CONTAINER=g4dev
  G4_DOCKER_WORKDIR=/work/g4optics/test/OpNovice2
  G4_DOCKER_PROJECT_ROOT=/work/g4optics
  G4_HOST_PROJECT_ROOT=<host repository root>
  G4_DOCKER_BUILD_DIR=build
  G4_DOCKER_BUILD_JOBS=4
  G4_FORCE_REBUILD=0
  G4_EXPECTED_VERSION=11.4.2           optional exact version guard
  G4_GEANT4_SETUP_SCRIPT=/path/to/geant4.sh
  N_EVENTS=100
  DRY_RUN=0
  SOURCE_MODE=auto
  PLOT_WITH_ROOT=1
  ROOT_COMMAND=root
  ROOT_PLOT_FIDUCIAL_LIMIT_MM=45
  G4RUN_MANAGER_TYPE=Serial
  UPDATE_LATEST=0                     isolate all existing latest files/links
  SCAN_RUNS_DIR=<container output directory>
  SCAN_RESULT_POINTER=<container file containing this run's absolute path>

SCAN_* and LATEST_* environment variables are forwarded to the scan. Host
plotting requires the result below G4_DOCKER_PROJECT_ROOT, mounted from
G4_HOST_PROJECT_ROOT. Explicit G4* data settings survive sourcing geant4.sh.
With G4_EXPECTED_VERSION set, every dataset setting must point to an existing
directory with the version required by that Geant4 build. The g4dev-1142
container refuses the preserved legacy build and build-gui directories.
After environment acceptance, outputs/environment/active.json supplies the
default container, build directory and expected version. An explicit different
G4_DOCKER_CONTAINER selects the legacy defaults unless separately overridden.
USAGE
}

case "${1:-}" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

i=0
while [[ "${i}" -lt "${#USER_ARGS[@]}" ]]; do
  arg="${USER_ARGS[${i}]}"
  case "${arg}" in
    --events)
      i=$((i + 1))
      if [[ "${i}" -ge "${#USER_ARGS[@]}" ]]; then
        echo "Missing value for --events" >&2
        exit 1
      fi
      N_EVENTS_VALUE="${USER_ARGS[${i}]}"
      ;;
    --events=*)
      N_EVENTS_VALUE="${arg#*=}"
      ;;
    --dry-run)
      DRY_RUN_VALUE="1"
      ;;
    --no-root-plots)
      PLOT_WITH_ROOT_VALUE="0"
      ;;
    --root-command)
      i=$((i + 1))
      if [[ "${i}" -ge "${#USER_ARGS[@]}" ]]; then
        echo "Missing value for --root-command" >&2
        exit 1
      fi
      ROOT_COMMAND_VALUE="${USER_ARGS[${i}]}"
      ;;
    --root-command=*)
      ROOT_COMMAND_VALUE="${arg#*=}"
      ;;
  esac
  i=$((i + 1))
done

case "${PLOT_WITH_ROOT_VALUE}" in
  1|true|TRUE|yes|YES|on|ON)
    PLOT_WITH_ROOT_VALUE="1"
    ;;
  0|false|FALSE|no|NO|off|OFF)
    PLOT_WITH_ROOT_VALUE="0"
    ;;
  *)
    echo "Invalid PLOT_WITH_ROOT: ${PLOT_WITH_ROOT_VALUE}. Use 1 or 0." >&2
    exit 1
    ;;
esac

case "${FORCE_REBUILD_VALUE}" in
  1|true|TRUE|yes|YES|on|ON)
    FORCE_REBUILD_VALUE="1"
    ;;
  0|false|FALSE|no|NO|off|OFF)
    FORCE_REBUILD_VALUE="0"
    ;;
  *)
    echo "Invalid G4_FORCE_REBUILD: ${FORCE_REBUILD_VALUE}. Use 1 or 0." >&2
    exit 1
    ;;
esac

RESULT_POINTER="${SCAN_RESULT_POINTER:-/tmp/g4optics-scan-result-$(date -u +%Y%m%dT%H%M%S)-$$}"
REMOVE_RESULT_POINTER="0"
if [[ -z "${SCAN_RESULT_POINTER:-}" ]]; then REMOVE_RESULT_POINTER="1"; fi
case "${RESULT_POINTER}" in
  /*) ;;
  *) RESULT_POINTER="${CONTAINER_WORKDIR}/${RESULT_POINTER}" ;;
esac

DOCKER_ENV=(
  -e "N_EVENTS=${N_EVENTS_VALUE}"
  -e "DRY_RUN=${DRY_RUN_VALUE}"
  -e "SOURCE_MODE=${SOURCE_MODE_VALUE}"
  -e "PLOT_WITH_ROOT=${PLOT_WITH_ROOT_VALUE}"
  -e "ROOT_COMMAND=${ROOT_COMMAND_VALUE}"
  -e "ROOT_PLOT_SKIP_LOCAL=1"
  -e "ROOT_PLOT_FIDUCIAL_LIMIT_MM=${ROOT_PLOT_FIDUCIAL_LIMIT_MM_VALUE}"
  -e "G4RUN_MANAGER_TYPE=${RUN_MANAGER_TYPE_VALUE}"
  -e "G4_EXPECTED_VERSION=${EXPECTED_VERSION_VALUE}"
  -e "SCAN_COMMAND_SCRIPT=./run_sipm_cavity_scan_docker.sh"
  -e "SCAN_GIT_COMMIT=${HOST_GIT_COMMIT}"
  -e "SCAN_GIT_BRANCH=${HOST_GIT_BRANCH}"
  -e "SCAN_GIT_DIRTY=${HOST_GIT_DIRTY}"
)
# Pass values as distinct argv elements; never reconstruct a shell command from
# environment contents. This also preserves archive/identity metadata supplied
# by the environment manager without requiring a second allowlist.
while IFS= read -r env_name; do
  case "${env_name}" in
    SCAN_*|LATEST_*|UPDATE_LATEST|G4*|Geant4_DIR)
      DOCKER_ENV+=(-e "${env_name}=${!env_name}")
      ;;
  esac
done < <(compgen -e)
DOCKER_ENV+=(-e "SCAN_RESULT_POINTER=${RESULT_POINTER}")

IFS= read -r -d '' CONTAINER_SCRIPT <<'CONTAINER_SCRIPT' || true
set -euo pipefail
cd "$1"
build_dir="$2"
build_jobs="$3"
force_rebuild="$4"
container_name="$5"
shift 5

# geant4.sh may replace dataset paths. Retain all explicitly supplied G4
# settings, including paths to the environment manager's verified datasets.
g4_names=()
g4_values=()
while IFS= read -r name; do
  case "$name" in
    G4*|Geant4_DIR) g4_names+=("$name"); g4_values+=("${!name}") ;;
  esac
done < <(compgen -e)
setup_script="${G4_GEANT4_SETUP_SCRIPT:-}"
if [[ -z "$setup_script" ]]; then
  config_command="$(command -v geant4-config || true)"
  if [[ -n "$config_command" && -f "$(dirname "$config_command")/geant4.sh" ]]; then
    setup_script="$(dirname "$config_command")/geant4.sh"
  else
    setup_candidates=()
    for candidate in /opt/geant4/bin/geant4.sh /opt/geant4/*/bin/geant4.sh /usr/local/bin/geant4.sh; do
      [[ ! -f "$candidate" ]] || setup_candidates+=("$candidate")
    done
    if [[ "${#setup_candidates[@]}" -eq 1 ]]; then setup_script="${setup_candidates[0]}"; fi
  fi
fi
if [[ -z "$setup_script" || ! -f "$setup_script" ]]; then
  echo "Cannot locate a unique geant4.sh; set G4_GEANT4_SETUP_SCRIPT explicitly." >&2
  exit 1
fi
set +u
source "$setup_script"
set -u
for ((index=0; index<${#g4_names[@]}; index++)); do
  export "${g4_names[index]}=${g4_values[index]}"
done
actual_version="$(geant4-config --version)"
if [[ -n "${G4_EXPECTED_VERSION:-}" && "$actual_version" != "$G4_EXPECTED_VERSION" ]]; then
  echo "Geant4 version mismatch: expected $G4_EXPECTED_VERSION, found $actual_version." >&2
  exit 1
fi
if [[ -n "${G4_EXPECTED_VERSION:-}" ]]; then
  python3 -B - <<'PY'
from pathlib import Path
import os
import re
import subprocess

# --datasets reports the compiled dataset paths, independently of runtime
# overrides: <NAME> <ENVVARNAME> <PATH>, one dataset per line.
lines = [line for line in subprocess.check_output(
    ['geant4-config', '--datasets'], text=True).splitlines() if line.strip()]
if not lines:
    raise SystemExit('Geant4 dataset verification failed: --datasets returned no datasets.')
errors = []
seen = set()
for line in lines:
    fields = line.split(None, 2)
    if len(fields) != 3 or not re.fullmatch(r'G4[A-Z0-9_]+DATA', fields[1]):
        errors.append(f'Unexpected geant4-config --datasets entry: {line!r}')
        continue
    name, variable, compiled_directory = fields
    if variable in seen:
        errors.append(f'Duplicate dataset environment variable: {variable}')
        continue
    seen.add(variable)
    expected_directory = Path(compiled_directory).name
    value = os.environ.get(variable)
    if not value:
        errors.append(f'{variable} is unset; {name} requires {expected_directory}.')
        continue
    directory = Path(value)
    if not directory.is_dir():
        errors.append(f'{variable}={value}: directory does not exist; expected {expected_directory}.')
    elif directory.name != expected_directory or directory.resolve().name != expected_directory:
        errors.append(f'{variable}={value}: dataset version conflicts with compiled {expected_directory}.')
if errors:
    raise SystemExit('Geant4 dataset verification failed before build:\n  ' + '\n  '.join(errors))
print(f'Geant4 dataset settings verified: {len(seen)} existing directories with matching versions.')
PY
fi
echo "Geant4 setup: $setup_script (version $actual_version)"
case "$build_dir" in
  /*) ;;
  *) build_dir="$PWD/$build_dir" ;;
esac
if [[ "$container_name" == "g4dev-1142" ]]; then
  python3 -B - "$build_dir" "$PWD" <<'PY'
from pathlib import Path
import sys
requested = Path(sys.argv[1]).resolve()
source = Path(sys.argv[2]).resolve()
if requested in {(source / 'build').resolve(), (source / 'build-gui').resolve()}:
    raise SystemExit(f'Refusing to rebuild preserved legacy directory with g4dev-1142: {requested}; select a separate versioned build directory.')
PY
fi
if [[ "$force_rebuild" == "1" || ! -f "$build_dir/CMakeCache.txt" ]]; then
  configure_args=(-S . -B "$build_dir")
  if [[ -n "${Geant4_DIR:-}" ]]; then configure_args+=("-DGeant4_DIR=$Geant4_DIR"); fi
  cmake "${configure_args[@]}"
fi
if [[ -n "${G4_EXPECTED_VERSION:-}" ]]; then
  python3 - "$build_dir/CMakeCache.txt" "$G4_EXPECTED_VERSION" <<'PY'
from pathlib import Path
import re
import sys
cache = Path(sys.argv[1])
match = re.search(r'^Geant4_DIR:[^=]+=(.+)$', cache.read_text(), re.MULTILINE)
if not match:
    raise SystemExit(f"Cannot verify Geant4_DIR in {cache}; configure a new build directory.")
version_file = Path(match.group(1)) / 'Geant4ConfigVersion.cmake'
version_text = version_file.read_text()
version = re.search(r'set\s*\(\s*PACKAGE_VERSION\s+"?([0-9.]+)', version_text, re.IGNORECASE)
if not version or version.group(1) != sys.argv[2]:
    raise SystemExit(f"Cached build Geant4 version differs from {sys.argv[2]}: {version_file}; use a new build directory.")
PY
fi
cmake --build "$build_dir" -j"$build_jobs"
export OPNOVICE2_EXECUTABLE="$build_dir/OpNovice2"
echo "Scan executable: $OPNOVICE2_EXECUTABLE"
./run_sipm_cavity_scan.sh "$@"
CONTAINER_SCRIPT

docker exec "${DOCKER_ENV[@]}" "${CONTAINER}" bash -c "${CONTAINER_SCRIPT}" \
  bash "${CONTAINER_WORKDIR}" "${CONTAINER_BUILD_DIR}" "${CONTAINER_BUILD_JOBS}" \
  "${FORCE_REBUILD_VALUE}" "${CONTAINER}" "$@"

CONTAINER_RUN_DIR="$(docker exec "${CONTAINER}" bash -c '
  set -euo pipefail
  cat "$1"
  if [[ "$2" == "1" ]]; then rm -f -- "$1"; fi
' bash "${RESULT_POINTER}" "${REMOVE_RESULT_POINTER}")"
CONTAINER_PROJECT_ROOT="${CONTAINER_PROJECT_ROOT%/}"
case "${CONTAINER_RUN_DIR}" in
  "${CONTAINER_PROJECT_ROOT}"/*)
    RUN_RELATIVE_PATH="${CONTAINER_RUN_DIR#"${CONTAINER_PROJECT_ROOT}"/}"
    ;;
  *)
    echo "Run directory is outside G4_DOCKER_PROJECT_ROOT: ${CONTAINER_RUN_DIR}" >&2
    exit 1
    ;;
esac
HOST_RUN_DIR="${HOST_PROJECT_ROOT%/}/${RUN_RELATIVE_PATH}"
if [[ ! -d "${HOST_RUN_DIR}" || ! -f "${HOST_RUN_DIR}/run_config.json" ]]; then
  echo "Run directory is not available at its host project mapping: ${HOST_RUN_DIR}" >&2
  exit 1
fi
HOST_RUN_DIR="$(cd "${HOST_RUN_DIR}" && pwd -P)"
echo "Resolved container run directory: ${CONTAINER_RUN_DIR}"
echo "Resolved host run directory: ${HOST_RUN_DIR}"

case "${DRY_RUN_VALUE}" in
  1|true|TRUE|yes|YES|on|ON) exit 0 ;;
esac
if [[ "${PLOT_WITH_ROOT_VALUE}" != "1" ]]; then exit 0; fi

if ! command -v "${ROOT_COMMAND_VALUE}" >/dev/null 2>&1; then
  echo "Host ROOT plot generation skipped: ROOT command not found: ${ROOT_COMMAND_VALUE}" >&2
  echo "Set ROOT_COMMAND=/path/to/root or PLOT_WITH_ROOT=0." >&2
  exit 0
fi

if [[ ! -f "plot_efficiency_map.C" ]]; then
  echo "Host ROOT plot generation skipped: missing plot_efficiency_map.C" >&2
  exit 0
fi

echo "Generating host ROOT plots from ${HOST_RUN_DIR}"
# ROOT receives a C++ string literal, so escape quotes and backslashes separately
# from the shell's argv quoting.
ROOT_RUN_LITERAL="$(python3 -B -c 'import json, sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "${HOST_RUN_DIR}")"
"${ROOT_COMMAND_VALUE}" -b -q "plot_efficiency_map.C(${ROOT_RUN_LITERAL},${ROOT_PLOT_FIDUCIAL_LIMIT_MM_VALUE})"
echo "Host ROOT plots: ${HOST_RUN_DIR}/root_*.png and root_*.pdf"
