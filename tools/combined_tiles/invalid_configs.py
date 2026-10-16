#!/usr/bin/env python3
"""Prepare, execute, or audit nine zero-event invalid combined-tile configurations."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import time

from common import command_lines, digest, read_json, require, write_json

SCHEMA = 'combined-tiles-invalid-configs-v1'
SPECIFICATIONS = (
    {'id': 'joint-zero', 'prefix': '/opnovice2/combined/layers',
     'replacement': '/opnovice2/combined/layers 8 0 8 mm', 'code': 'OpNovice2_Combined_001',
     'diagnostic': 'front, joint, and back thicknesses must be finite and positive'},
    {'id': 'front-negative', 'prefix': '/opnovice2/combined/layers',
     'replacement': '/opnovice2/combined/layers -8 0.1 8 mm', 'code': 'OpNovice2_Combined_001',
     'diagnostic': 'front, joint, and back thicknesses must be finite and positive'},
    {'id': 'active-thickness-mismatch', 'prefix': '/opnovice2/tank/size',
     'replacement': '/opnovice2/tank/size 100 100 24 mm', 'code': 'OpNovice2_Combined_002',
     'diagnostic': 'tank/size must be 100 100 (front+back) mm, excluding joint thickness'},
    {'id': 'layout-single', 'prefix': '/opnovice2/sipm/layout',
     'replacement': '/opnovice2/sipm/layout single', 'code': 'OpNovice2_Combined_002',
     'diagnostic': 'require the edge-two SiPM layout on +X'},
    {'id': 'old-grease-enabled', 'prefix': '/opnovice2/grease/enabled',
     'replacement': '/opnovice2/grease/enabled true', 'code': 'OpNovice2_Combined_002',
     'diagnostic': 'require stack, absorber, dimple, bottom cavity, and SiPM grease disabled'},
    {'id': 'surface-wrapped', 'prefix': '/opnovice2/surfaceFinish',
     'replacement': '/opnovice2/surfacePreset wrapped', 'code': 'OpNovice2_Combined_002',
     'diagnostic': 'require the bare polished surface preset without optical surface overrides'},
    {'id': 'missing-joint-rindex', 'prefix': '/opnovice2/greaseProperty RINDEX',
     'replacement': None, 'code': 'OpNovice2_Combined_002',
     'diagnostic': 'requires greaseProperty RINDEX with at least two entries'},
    {'id': 'missing-joint-abslength', 'prefix': '/opnovice2/greaseProperty ABSLENGTH',
     'replacement': None, 'code': 'OpNovice2_Combined_002',
     'diagnostic': 'requires greaseProperty ABSLENGTH with at least two entries'},
    {'id': 'joint-rindex-short-spectrum', 'prefix': '/opnovice2/greaseProperty RINDEX',
     'replacement': '/opnovice2/greaseProperty RINDEX 0.0000027 1.46 0.0000029 1.46',
     'code': 'OpNovice2_Combined_002',
     'diagnostic': 'RINDEX must cover the scintillation emission spectrum'},
)
FATAL_RE = re.compile(r'FatalException|Fatal Exception|Fatal Error', re.IGNORECASE)
UNRELATED_FAILURE_RE = re.compile(
    r'COMMAND NOT FOUND|command not found|Illegal application state|'
    r'command refused|Can not open a macro file|cannot open.*macro|'
    r'error while loading shared libraries|No such file or directory', re.IGNORECASE)
EVENT_START_RE = re.compile(
    r'###\s*Run\s+\d+\s+starts|Begin of event|EventID\s*[:=]?\s*\d+|'
    r'(?:Number of events processed|# of primary particles)\s*:\s*[1-9]\d*', re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def zero_event_commands(text: str) -> list[str]:
    lines = command_lines(text)
    require(not any(line.split()[0] == '/run/beamOn' for line in lines), 'invalid case must have no beamOn')
    require(not any(line.split()[0] in ('/control/execute', '/control/loop', '/control/foreach', '/control/shell')
                    for line in lines), 'nested macros, loops, and shell commands are forbidden')
    require(lines.count('/run/initialize') == 1, 'expected exactly one initialize')
    require(lines[-1] == '/run/initialize', 'invalid macro must end at initialization')
    return lines


def make_macro(smoke_macro: str, specification: dict) -> str:
    commands = command_lines(smoke_macro)
    require(commands.count('/run/initialize') == 1, 'smoke initialization must be unique')
    commands = commands[:commands.index('/run/initialize') + 1]
    require('/opnovice2/combined/enabled true' in commands, 'smoke base is not combined')
    require('/opnovice2/combined/layers 8 0.1 8 mm' in commands, 'base must be joined8-8')
    matches = [i for i, line in enumerate(commands) if line.startswith(specification['prefix'] + ' ')]
    require(len(matches) == 1, 'mutation must match exactly one command: ' + specification['id'])
    index = matches[0]
    if specification['replacement'] is None:
        del commands[index]
    else:
        commands[index] = specification['replacement']
    text = '# ' + SCHEMA + ': ' + specification['id'] + '\n' + '\n'.join(commands) + '\n'
    zero_event_commands(text)
    return text


def prepare(smoke: Path, output: Path) -> dict:
    smoke, output = smoke.resolve(), output.resolve()
    require(not output.exists(), f'refusing to overwrite existing output: {output}')
    source_manifest = read_json(smoke / 'manifest.json')
    require(source_manifest['phase'] == 'smoke', 'base campaign must be smoke')
    bases = [task for task in source_manifest['tasks'] if task['case_id'] == 'joined8-8']
    require(len(bases) == 1, 'expected exactly one joined8-8 smoke task')
    base_task = bases[0]
    base_macro = smoke / base_task['id'] / 'run.mac'
    require(digest(base_macro) == base_task['macro_sha256'], 'smoke macro hash changed')
    base_case = next(case for case in source_manifest['cases'] if case['id'] == 'joined8-8')
    require(base_case['front_mm'] == base_case['back_mm'] == 8 and base_case['gap_mm'] == 0.1,
            'unexpected base geometry')
    require(len(SPECIFICATIONS) <= 10, 'invalid-case process budget exceeded')
    macros = [(entry, make_macro(base_macro.read_text(), entry)) for entry in SPECIFICATIONS]
    manifest = {
        'schema_version': SCHEMA, 'phase': 'invalid', 'scope': 'configuration rejection; zero physics events',
        'base_commit': source_manifest['base_commit'], 'working_head': source_manifest['working_head'],
        'source_root_at_preparation': source_manifest['source_root_at_preparation'],
        'source_files_sha256': source_manifest['source_files_sha256'],
        'base_smoke': {'path': str(smoke), 'manifest_sha256': digest(smoke / 'manifest.json'),
                       'task_id': base_task['id'], 'macro_sha256': digest(base_macro)},
        'invalid_tool_sha256': digest(Path(__file__)),
        'execution': {'total_events': 0, 'max_authorized_invalid_cases': 10,
                      'one_process_per_task': True, 'run_manager': 'Serial',
                      'retry_policy': 'none; each new run requires a fresh output directory'},
        'cases': [{**base_case, 'id': entry['id'], 'invalid': entry} for entry in SPECIFICATIONS],
        'tasks': [],
    }
    output.mkdir(parents=True)
    for specification, macro in macros:
        task_id = specification['id'] + '/block-00'
        folder = output / task_id
        folder.mkdir(parents=True)
        (folder / 'run.mac').write_text(macro)
        manifest['tasks'].append({'id': task_id, 'case_id': specification['id'], 'block': 0,
                                  'events': 0, 'seeds': None, 'macro_sha256': digest(folder / 'run.mac'),
                                  'expected_rejection': specification})
    write_json(output / 'manifest.json', manifest)
    return manifest


def checked_tasks(campaign: Path) -> tuple[dict, list[tuple[dict, Path]]]:
    manifest = read_json(campaign / 'manifest.json')
    require(manifest['schema_version'] == SCHEMA and manifest['phase'] == 'invalid', 'wrong manifest')
    tasks = manifest['tasks']
    expected = {entry['id']: entry for entry in SPECIFICATIONS}
    require(len(tasks) == len(expected) and len(tasks) <= 10, 'wrong invalid-case count')
    require({task['case_id'] for task in tasks} == set(expected), 'wrong invalid-case IDs')
    checked = []
    for task in tasks:
        folder = (campaign / task['id']).resolve()
        require(campaign in folder.parents, 'task path escapes campaign')
        require(task['events'] == 0, 'invalid task requests physics events')
        require(task['expected_rejection'] == expected[task['case_id']], 'expected diagnostic changed')
        require(digest(folder / 'run.mac') == task['macro_sha256'], 'macro drift: ' + task['id'])
        zero_event_commands((folder / 'run.mac').read_text())
        checked.append((task, folder))
    return manifest, checked


def disable_core_dumps():
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(campaign: Path, executable: Path, timeout: float) -> list[dict]:
    campaign, executable = campaign.resolve(), executable.resolve()
    require(executable.is_file() and os.access(executable, os.X_OK), 'executable is missing or not executable')
    require(0 < timeout <= 300, 'timeout must be in (0, 300] seconds per process')
    _, tasks = checked_tasks(campaign)
    for _, folder in tasks:
        require(not any((folder / name).exists() for name in ('run.log', 'receipt.json', 'result.root', 'result_summary.csv')),
                'refusing to rerun existing evidence: ' + str(folder))
    executable_hash = digest(executable)
    manifest_hash = digest(campaign / 'manifest.json')
    receipts = []
    env = os.environ.copy()
    env['G4RUN_MANAGER_TYPE'] = 'Serial'
    for task, folder in tasks:
        require(digest(executable) == executable_hash, 'executable changed during campaign')
        command = [str(executable), 'run.mac']
        started = utc_now()
        start = time.monotonic()
        timed_out, launch_error, returncode = False, None, None
        with (folder / 'run.log').open('x', encoding='utf-8') as log:
            try:
                result = subprocess.run(command, cwd=folder, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        timeout=timeout, check=False,
                                        preexec_fn=disable_core_dumps if os.name == 'posix' else None)
                returncode = result.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
            except OSError as error:
                launch_error = str(error)
        receipt = {'schema_version': SCHEMA, 'task_id': task['id'], 'started_utc': started,
                   'finished_utc': utc_now(), 'wall_seconds': time.monotonic() - start,
                   'command': command, 'cwd': str(folder), 'returncode': returncode,
                   'timed_out': timed_out, 'launch_error': launch_error,
                   'G4RUN_MANAGER_TYPE': 'Serial', 'requested_events': 0,
                   'executable_sha256': executable_hash, 'macro_sha256': task['macro_sha256'],
                   'manifest_sha256': manifest_hash, 'log_sha256': digest(folder / 'run.log')}
        write_json(folder / 'receipt.json', receipt)
        receipts.append(receipt)
        print(f"{task['case_id']}: returncode={returncode}, timed_out={timed_out}", flush=True)
    return receipts


def evaluate_log(log: str, returncode: int | None, expected: dict) -> list[str]:
    failures = []
    if expected['code'] not in log:
        failures.append('expected Geant4 exception code missing')
    if expected['diagnostic'].lower() not in log.lower():
        failures.append('expected configuration diagnostic missing')
    if returncode in (None, 0) and not FATAL_RE.search(log):
        failures.append('no nonzero exit or explicit fatal rejection')
    if UNRELATED_FAILURE_RE.search(log):
        failures.append('unrelated command, macro, or executable failure')
    if EVENT_START_RE.search(log):
        failures.append('physics event execution evidence found')
    if any(line.split()[0] == '/run/beamOn' for line in command_lines(log)):
        failures.append('beamOn command found in runtime log')
    return failures


def audit(campaign: Path, output: Path) -> dict:
    campaign, output = campaign.resolve(), output.resolve()
    require(not output.exists(), 'refusing to overwrite an audit report')
    _, tasks = checked_tasks(campaign)
    manifest_hash = digest(campaign / 'manifest.json')
    results = []
    executable_hashes = set()
    for task, folder in tasks:
        failures = []
        receipt_path, log_path = folder / 'receipt.json', folder / 'run.log'
        if not receipt_path.exists() or not log_path.exists():
            failures.append('execution receipt or log missing')
            receipt = {}
        else:
            receipt = read_json(receipt_path)
            log = log_path.read_text(errors='replace')
            if receipt.get('schema_version') != SCHEMA or receipt.get('task_id') != task['id']:
                failures.append('receipt identity mismatch')
            if receipt.get('timed_out') or receipt.get('launch_error'):
                failures.append('process timed out or failed to launch')
            if receipt.get('requested_events') != 0 or receipt.get('G4RUN_MANAGER_TYPE') != 'Serial':
                failures.append('receipt execution contract mismatch')
            for field, value in [('manifest_sha256', manifest_hash), ('macro_sha256', task['macro_sha256']),
                                 ('log_sha256', digest(log_path))]:
                if receipt.get(field) != value:
                    failures.append(field + ' mismatch')
            exe_hash = receipt.get('executable_sha256')
            if not isinstance(exe_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', exe_hash):
                failures.append('executable hash missing or malformed')
            else:
                executable_hashes.add(exe_hash)
            failures.extend(evaluate_log(log, receipt.get('returncode'), task['expected_rejection']))
        if any((folder / name).exists() for name in ('result.root', 'result_summary.csv')):
            failures.append('unexpected physics-run result artifact')
        results.append({'task_id': task['id'], 'accepted': not failures,
                        'expected_code': task['expected_rejection']['code'],
                        'expected_diagnostic': task['expected_rejection']['diagnostic'],
                        'returncode': receipt.get('returncode'), 'failures': failures})
    report = {'schema_version': SCHEMA, 'campaign': str(campaign), 'manifest_sha256': manifest_hash,
              'accepted': all(item['accepted'] for item in results) and len(executable_hashes) == 1,
              'case_count': len(results), 'requested_physics_events': 0,
              'executable_hashes': sorted(executable_hashes), 'cases': results}
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--smoke-campaign', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    execute = sub.add_parser('run')
    execute.add_argument('--campaign', type=Path, required=True)
    execute.add_argument('--exe', type=Path, required=True)
    execute.add_argument('--timeout-seconds', type=float, default=60)
    check = sub.add_parser('audit')
    check.add_argument('--campaign', type=Path, required=True)
    check.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        manifest = prepare(args.smoke_campaign, args.output)
        print(f"Prepared {len(manifest['tasks'])} invalid cases; zero physics events; no simulation executed.")
    elif args.action == 'run':
        run(args.campaign, args.exe, args.timeout_seconds)
    else:
        report = audit(args.campaign, args.output)
        print(f"Invalid-case audit accepted={report['accepted']}; cases={report['case_count']}")
        raise SystemExit(0 if report['accepted'] else 1)


if __name__ == '__main__':
    main()
