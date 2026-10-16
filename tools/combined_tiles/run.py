#!/usr/bin/env python3
"""Run each frozen task once in its own process; use from the selected Docker runtime."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import time

from audit import validate_inputs as validate_study_inputs
from common import digest, read_json, require, write_json


def ui_failures(text: str) -> list[str]:
    """Geant4 UI errors can terminate a macro while the executable returns zero."""
    pattern = re.compile(
        r'COMMAND NOT FOUND|COMMAND REFUSED|command failed|command failure|'
        r'\*{5}\s*Illegal|illegal application state|illegal parameter|'
        r'parameter (?:is )?out of range|FatalException|fatal error|'
        r'Run must be terminated|Batch is interrupted|Segmentation fault|'
        r'\bAborted\b|Overlap is detected', re.IGNORECASE)
    return [line.strip() for line in text.splitlines() if pattern.search(line)]


def run(campaign: Path, repo: Path, executable: Path, qualified_receipt: Path | None = None,
        timeout: int = 900):
    campaign, repo, executable = campaign.resolve(), repo.resolve(), executable.resolve()
    if read_json(campaign / 'manifest.json')['phase'] == 'probe':
        from probes import validate_inputs as validate_inputs_for_phase
    else:
        validate_inputs_for_phase = validate_study_inputs
    manifest = validate_inputs_for_phase(campaign, repo)
    executable_hash = digest(executable)
    qualification = None
    if manifest['phase'] != 'geometry':
        require(qualified_receipt is not None, 'non-geometry execution requires --qualified-receipt')
        q = read_json(qualified_receipt)
        require(q['status'] == 'passed', 'qualification did not pass')
        require(q['readout_mode'] == manifest['configuration']['readout_mode'], 'qualification used different readout mode')
        qualified_sources = dict(manifest['source_files_sha256'])
        if manifest['phase'] == 'probe':
            qualified_sources.pop('tools/combined_tiles/probes.py', None)
        require(q['source_files_sha256'] == qualified_sources, 'qualification used different source inputs')
        require(q['executable_sha256'] == executable_hash, 'qualification used different executable')
        if manifest['phase'] == 'smoke':
            require(q['phase'] == 'geometry', 'smoke requires geometry audit')
        elif manifest['phase'] == 'probe':
            require(q['phase'] == 'smoke', 'physics probes require smoke audit')
        else:
            require(q.get('smoke_status') == 'passed' and q.get('physics_probes_status') == 'passed',
                    'exploration requires passed smoke and physics probes')
        qualification = {'path': str(qualified_receipt.resolve()), 'sha256': digest(qualified_receipt)}
    # Check the whole matrix before claiming its execution. Partial failures are retained and never retried here.
    for task in manifest['tasks']:
        folder = campaign / task['id']
        require(not any((folder / name).exists() for name in ('receipt.json', 'simulation.log', 'result.root', 'result_summary.csv')),
                f'previous execution evidence exists: {folder}')
    require(os.environ.get('G4RUN_MANAGER_TYPE') == 'Serial', 'set G4RUN_MANAGER_TYPE=Serial')
    write_json(campaign / 'execution.json', {
        'manifest_sha256': digest(campaign / 'manifest.json'), 'executable_sha256': executable_hash,
        'executable_at_execution': str(executable), 'qualification': qualification,
        'readout_mode': manifest['configuration']['readout_mode'],
        'run_manager': os.environ.get('G4RUN_MANAGER_TYPE'), 'timeout_seconds_per_task': timeout,
        'runtime_environment': {key: value for key, value in os.environ.items()
                                if key.startswith('G4') or key == 'GEANT4_DATA_DIR'}})
    for task in manifest['tasks']:
        validate_inputs_for_phase(campaign, repo)
        require(digest(executable) == executable_hash, 'executable changed during campaign')
        folder = campaign / task['id']; start = time.monotonic()
        error = None
        with (folder / 'simulation.log').open('x') as log:
            try:
                result = subprocess.run([str(executable), 'run.mac'], cwd=folder,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code, error = 124, 'timeout; process terminated, no automatic retry'
        ui_failure = ui_failures((folder / 'simulation.log').read_text(errors='replace'))
        missing_outputs = [name for name in ('result.root', 'result_summary.csv')
                           if task['events'] > 0 and (not (folder / name).is_file() or (folder / name).stat().st_size == 0)]
        receipt = {'exit_code': code, 'error': error,
                   'status': 'passed' if code == 0 and not ui_failure and not missing_outputs else 'failed',
                   'ui_failure': ui_failure, 'missing_or_empty_outputs': missing_outputs,
                   'elapsed_seconds': time.monotonic() - start,
                   'manifest_sha256': digest(campaign / 'manifest.json'),
                   'macro_sha256': digest(folder / 'run.mac'), 'log_sha256': digest(folder / 'simulation.log'),
                   'executable_sha256': digest(executable), 'qualification': qualification,
                   'events_requested': task['events'], 'seeds': task['seeds'],
                   'readout_mode': manifest['configuration']['readout_mode'],
                   'execution_sha256': digest(campaign / 'execution.json')}
        write_json(folder / 'receipt.json', receipt)
        print(json.dumps({'task': task['id'], **receipt}), flush=True)
        require(code == 0, f"{task['id']} failed with exit {code}; campaign stopped")
        require(not ui_failure, f"{task['id']} failed despite exit {code}: {'; '.join(ui_failure)}; campaign stopped")
        require(not missing_outputs, f"{task['id']} missing or empty event outputs: {missing_outputs}; campaign stopped")
    validate_inputs_for_phase(campaign, repo)
    require(digest(executable) == executable_hash, 'executable changed during campaign')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--qualified-receipt', type=Path)
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    run(args.campaign, args.repo, args.executable, args.qualified_receipt, args.timeout)


if __name__ == '__main__':
    main()
