#!/usr/bin/env bash
# Compatibility entrypoint; existing frozen campaign copies are unaffected.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_ROOT="${G4_DATA_ROOT:-${HOME}/geant4-data/11.4.2}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  DATA_ROOT="$1"
  shift
fi
exec "${G4_DATA_INSTALL_PYTHON:-python3}" \
  "${SCRIPT_DIR}/../../tools/local/install_datasets.py" "${DATA_ROOT}" "$@"
