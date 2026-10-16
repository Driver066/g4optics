#!/usr/bin/env python3
"""Exact, bounded Side2 regression against frozen v2 data; never reuse an output tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

SCHEMA = 'combined-layouts-side2-regression-v1'
CASES = ('mono16', 'mono24', 'joined8-8', 'joined8-16', 'joined16-8')
REFERENCES = (('legacy-smoke', 'legacy'), ('smoke', 'boundary-entry'))
VARIANTS = ('omitted', 'enabled')
INPUT_FILES = ('run.mac', 'result.root', 'result_summary.csv', 'currentEvent.rndm',
               'currentRun.rndm', 'receipt.json', 'simulation.log')
OUTPUT_FILES = ('run.mac', 'result.root', 'result_summary.csv', 'currentEvent.rndm',
                'currentRun.rndm', 'receipt.json', 'simulation.log')
EXTRA_COLUMNS = {
    'combined_events': {f'{origin}_to_sensor{i}' for origin in ('lineage_front', 'lineage_back', 'unknown')
                        for i in (2, 3)},
    'readout_events': {f'sensor{i}_{field}' for i in (2, 3)
                       for field in ('entries', 'reflections', 'step_too_small')},
}
UI_FAILURE = re.compile(
    r'COMMAND NOT FOUND|COMMAND REFUSED|command fail(?:ed|ure)|\*{5}\s*Illegal|'
    r'illegal application state|illegal parameter|parameter (?:is )?out of range|'
    r'FatalException|Fatal Exception|fatal error|Run must be terminated|Batch is interrupted|'
    r'Segmentation fault|\bAborted\b|Overlap is detected', re.IGNORECASE)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write('\n')


def safe_child(root, relative):
    root = Path(root).resolve()
    child = (root / relative).resolve()
    require(root in child.parents, 'path escapes evidence root: ' + str(relative))
    return child


def command_lines(text):
    return [part for line in text.splitlines()
            if (part := line.partition('#')[0].strip()).startswith('/')]


def make_macro(original: bytes, variant: str, mode: str, seeds: list[int]) -> bytes:
    require(variant in VARIANTS and mode in ('legacy', 'boundary-entry'), 'invalid regression variant/mode')
    text = original.decode('utf-8')
    commands = command_lines(text)
    require(not any(line.startswith('/opnovice2/combined/layoutStudy') for line in commands),
            'reference already changes standalone layout study')
    require(commands.count('/run/initialize') == 1, 'reference initialize is not unique')
    require([line for line in commands if line.startswith('/run/beamOn')] == ['/run/beamOn 10'],
            'reference must request exactly ten events')
    require([line for line in commands if line.startswith('/opnovice2/sipm/readoutMode ')] ==
            ['/opnovice2/sipm/readoutMode ' + mode], 'reference readout mode differs')
    require('/opnovice2/sipm/layout edge-two' in commands and '/opnovice2/sipm/face +X' in commands,
            'reference must use Side2 on +X')
    require(not any(line.split()[0] in ('/control/execute', '/control/loop', '/control/foreach', '/control/shell')
                    for line in commands), 'nested execution is not allowed')
    geometry = commands.index('/geometry/test/run')
    beam = commands.index('/run/beamOn 10')
    resets = [line for line in commands[geometry + 1:beam] if line.startswith('/random/setSeeds ')]
    require(resets == ['/random/setSeeds ' + ' '.join(map(str, seeds))],
            'reference must reset its exact physics seed pair after geometry checks')
    if variant == 'omitted':
        return original
    lines = text.splitlines(keepends=True)
    index = next(i for i, line in enumerate(lines) if line.partition('#')[0].strip() == '/run/initialize')
    lines.insert(index, '/opnovice2/combined/layoutStudy true\n')
    return ''.join(lines).encode('utf-8')


def reference_inventory(reference: Path) -> tuple[dict, list[dict], dict]:
    reference = reference.resolve()
    files, tasks, sources = {}, [], {}
    binaries = set()
    for phase, mode in REFERENCES:
        manifest_path = reference / phase / 'manifest.json'
        report_path = reference / (phase + '-audit.json')
        manifest, report = read_json(manifest_path), read_json(report_path)
        require(manifest['phase'] == report['phase'] == 'smoke' and report['status'] == 'passed',
                'reference is not an audited smoke campaign: ' + phase)
        require(manifest['configuration']['readout_mode'] == report['readout_mode'] == mode,
                'reference mode mismatch')
        require(report['manifest_sha256'] == digest(manifest_path), 'reference audit/manifest mismatch')
        require(report['source_files_sha256'] == manifest['source_files_sha256'], 'reference source inventory mismatch')
        sources[phase] = manifest['source_files_sha256']  # Provenance only; never compare with changed live C++.
        require(len(manifest['tasks']) == len(CASES), 'reference has wrong task count')
        require({task['id'] for task in manifest['tasks']} == {case + '/block-00' for case in CASES},
                'reference task matrix differs')
        require({task['task_id'] for task in report['tasks']} == {case + '/block-00' for case in CASES},
                'reference audit task coverage differs')
        for relative in (phase + '/manifest.json', phase + '/execution.json', phase + '-audit.json'):
            files[relative] = digest(reference / relative)
        for case in CASES:
            task = next(t for t in manifest['tasks'] if t['case_id'] == case)
            require(task['id'] == case + '/block-00' and task['block'] == 0 and task['events'] == 10,
                    'reference task has changed identity or event count')
            relative = phase + '/' + task['id']
            folder = safe_child(reference, relative)
            receipt = read_json(folder / 'receipt.json')
            require(receipt['status'] == 'passed' and receipt['exit_code'] == 0 and
                    not receipt.get('error') and not receipt.get('ui_failure'), 'reference task did not pass')
            require(receipt['macro_sha256'] == task['macro_sha256'] == digest(folder / 'run.mac'),
                    'reference macro is not bound to its task')
            require(receipt['manifest_sha256'] == digest(manifest_path) and
                    receipt['log_sha256'] == digest(folder / 'simulation.log'), 'reference receipt drift')
            require(receipt['execution_sha256'] == files[phase + '/execution.json'], 'reference execution receipt drift')
            require(receipt['executable_sha256'] == report['executable_sha256'], 'reference binary identity mismatch')
            binaries.add(report['executable_sha256'])
            old_record = next(t for t in report['tasks'] if t['task_id'] == task['id'])
            for name, expected in old_record['files_sha256'].items():
                require(digest(safe_child(folder, name)) == expected, 'reference audited artifact drift: ' + name)
            for name in INPUT_FILES:
                files[relative + '/' + name] = digest(folder / name)
            original = (folder / 'run.mac').read_bytes()
            make_macro(original, 'omitted', mode, task['seeds'])
            tasks.append({'reference_task': relative, 'case_id': case, 'mode': mode,
                          'events': 10, 'seeds': task['seeds'], 'reference_macro_sha256': digest(folder / 'run.mac')})
    require(len(binaries) == 1, 'reference campaigns mixed executable identities')
    return files, tasks, sources


def prepare(reference: Path, output: Path) -> dict:
    reference, output = reference.resolve(), output.resolve()
    require(not output.exists(), 'refusing to overwrite regression output')
    files, references, sources = reference_inventory(reference)
    macros = []
    for variant in VARIANTS:
        for task in references:
            original = (reference / task['reference_task'] / 'run.mac').read_bytes()
            macros.append((variant, task, make_macro(original, variant, task['mode'], task['seeds'])))
    manifest = {'schema_version': SCHEMA, 'phase': 'side2-regression',
                'scope': '200 engineering beta events; excluded from the 8000-event scientific matrix',
                'reference_root_at_preparation': str(reference), 'reference_files_sha256': files,
                'reference_source_files_sha256': sources, 'regression_tool_sha256': digest(Path(__file__)),
                'execution': {'total_events': 200, 'task_count': 20, 'events_per_task': 10,
                              'run_manager': 'Serial', 'automatic_retries': 0},
                'reference_macros': {}, 'tasks': []}
    output.mkdir(parents=True)
    for task in references:
        relative = task['reference_task'] + '/run.mac'
        copy = 'reference-macros/' + relative
        path = safe_child(output, copy)
        path.parent.mkdir(parents=True)
        path.write_bytes((reference / relative).read_bytes())
        manifest['reference_macros'][relative] = copy
    for variant, task, macro in macros:
        identity = variant + '/' + task['mode'] + '/' + task['case_id'] + '/block-00'
        folder = output / identity
        folder.mkdir(parents=True)
        (folder / 'run.mac').write_bytes(macro)
        manifest['tasks'].append({**task, 'id': identity, 'variant': variant,
                                  'layout_study': variant == 'enabled', 'macro_sha256': digest(folder / 'run.mac')})
    write_json(output / 'manifest.json', manifest)
    return manifest


def validate_inputs(campaign: Path) -> dict:
    campaign = campaign.resolve()
    manifest = read_json(campaign / 'manifest.json')
    require(manifest['schema_version'] == SCHEMA and manifest['phase'] == 'side2-regression', 'wrong regression manifest')
    require(manifest['execution'] == {'total_events': 200, 'task_count': 20, 'events_per_task': 10,
                                     'run_manager': 'Serial', 'automatic_retries': 0}, 'regression budget changed')
    require(manifest['regression_tool_sha256'] == digest(Path(__file__)), 'regression tool changed after preparation')
    expected = {f'{variant}/{mode}/{case}/block-00' for variant in VARIANTS
                for _, mode in REFERENCES for case in CASES}
    require(len(manifest['tasks']) == 20 and {t['id'] for t in manifest['tasks']} == expected,
            'regression task matrix changed')
    for task in manifest['tasks']:
        phase = next(phase for phase, mode in REFERENCES if mode == task['mode'])
        require(task['events'] == 10 and task['reference_task'] == f'{phase}/{task["case_id"]}/block-00' and
                task['id'] == f'{task["variant"]}/{task["mode"]}/{task["case_id"]}/block-00' and
                task['layout_study'] == (task['variant'] == 'enabled'), 'regression task identity changed')
        relative = task['reference_task'] + '/run.mac'
        copied = safe_child(campaign, manifest['reference_macros'][relative])
        require(digest(copied) == manifest['reference_files_sha256'][relative] == task['reference_macro_sha256'],
                'reference macro copy drift')
        macro = safe_child(campaign, task['id']) / 'run.mac'
        require(digest(macro) == task['macro_sha256'] and macro.read_bytes() ==
                make_macro(copied.read_bytes(), task['variant'], task['mode'], task['seeds']),
                'regression macro differs from exact allowed transformation')
    return manifest


def log_failures(text: str, task: dict) -> list[str]:
    failures = [line.strip() for line in text.splitlines() if UI_FAILURE.search(line)]
    codes = re.findall(r'\*\*\* G4Exception : ([^\s]+)', text)
    if set(codes) - {'Analysis_W001'}:
        failures.append('unexpected Geant4 exception')
    if 'Geometry overlaps check completed !' not in text:
        failures.append('geometry check did not complete')
    if text.count('SiPM readout mode: ' + task['mode']) != 1:
        failures.append('readout mode log mismatch')
    placements = [line for line in text.splitlines() if line.startswith('SiPM placement:')]
    if len(placements) != 2 or any(not any(f'copy={i}, face=+X, local=({y},0,0) mm, world=(50.25,{y},0) mm'
                                           in line for line in placements) for i, y in enumerate((-25, 25))):
        failures.append('Side2 physical sensor placement differs')
    active = 'Standalone layout geometry: PASS' in text
    if active != task['layout_study']:
        failures.append('standalone opt-in runtime state differs')
    return failures


def run(campaign: Path, executable: Path, timeout: float = 900):
    campaign, executable = campaign.resolve(), executable.resolve()
    manifest = validate_inputs(campaign)
    require(executable.is_file() and os.access(executable, os.X_OK), 'executable is missing or not executable')
    require(0 < timeout <= 3600, 'timeout must be positive and no more than 3600 seconds')
    require(not (campaign / 'execution.json').exists(), 'regression already dispatched; use a fresh output tree')
    for task in manifest['tasks']:
        folder = safe_child(campaign, task['id'])
        require(not any((folder / name).exists() for name in OUTPUT_FILES if name != 'run.mac'),
                'prior execution evidence: ' + task['id'])
    executable_hash, manifest_hash = digest(executable), digest(campaign / 'manifest.json')
    env = os.environ.copy()
    env['G4RUN_MANAGER_TYPE'] = 'Serial'
    write_json(campaign / 'execution.json', {'schema_version': SCHEMA, 'manifest_sha256': manifest_hash,
               'executable_sha256': executable_hash, 'executable_path': str(executable), 'run_manager': 'Serial',
               'timeout_seconds': timeout, 'automatic_retries': 0,
               'runtime_environment': {key: value for key, value in env.items()
                                       if key.startswith('G4') or key == 'GEANT4_DATA_DIR'}})
    execution_hash = digest(campaign / 'execution.json')
    for task in manifest['tasks']:
        validate_inputs(campaign)
        require(digest(executable) == executable_hash, 'binary drift before dispatch')
        folder = campaign / task['id']
        start = time.monotonic()
        code, error = None, None
        with (folder / 'simulation.log').open('x') as log:
            try:
                result = subprocess.run([str(executable), 'run.mac'], cwd=folder, env=env,
                                        stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
                code = result.returncode
            except subprocess.TimeoutExpired:
                error, code = 'timeout; terminated with no retry', 124
            except OSError as exc:
                error = str(exc)
        failures = log_failures((folder / 'simulation.log').read_text(errors='replace'), task)
        missing = [name for name in ('result.root', 'result_summary.csv', 'currentEvent.rndm', 'currentRun.rndm')
                   if not (folder / name).is_file() or (folder / name).stat().st_size == 0]
        if digest(executable) != executable_hash:
            failures.append('binary drift during execution')
        receipt = {'schema_version': SCHEMA, 'status': 'passed' if code == 0 and not error and not failures and not missing else 'failed',
                   'task_id': task['id'], 'variant': task['variant'], 'mode': task['mode'], 'exit_code': code,
                   'error': error, 'ui_failure': failures, 'missing_or_empty_outputs': missing,
                   'elapsed_seconds': time.monotonic() - start, 'events_requested': 10, 'seeds': task['seeds'],
                   'run_manager': 'Serial', 'manifest_sha256': manifest_hash, 'execution_sha256': execution_hash,
                   'executable_sha256': executable_hash, 'macro_sha256': digest(folder / 'run.mac'),
                   'log_sha256': digest(folder / 'simulation.log')}
        write_json(folder / 'receipt.json', receipt)
        print(json.dumps({'task': task['id'], 'status': receipt['status'], 'exit_code': code}), flush=True)
        require(receipt['status'] == 'passed', 'regression stopped after failed task: ' + task['id'])
    validate_inputs(campaign)
    require(digest(executable) == executable_hash, 'binary changed after regression')


def exact_array(left, right, description):
    import numpy as np
    a, b = np.asarray(left), np.asarray(right)
    require(a.dtype == b.dtype and a.shape == b.shape, 'array schema differs: ' + description)
    require(np.array_equal(a, b, equal_nan=True) if a.dtype.kind in 'fc' else np.array_equal(a, b),
            'array values differ: ' + description)


def compare_table_arrays(name: str, old: dict, new: dict, *, variant: str, mode: str, events: int = 10) -> dict:
    import numpy as np
    require(set(old) <= set(new), 'original table columns missing: ' + name)
    extras = set(new) - set(old)
    require(extras <= EXTRA_COLUMNS.get(name, set()), 'unapproved additional columns: ' + name)
    for key in extras:
        require(np.isfinite(new[key]).all() and (new[key] == 0).all(), 'inactive sensor column is nonzero: ' + name + '/' + key)
    old_n, new_n = len(old['event_id']), len(new['event_id'])
    require(all(len(value) == old_n for value in old.values()) and all(len(value) == new_n for value in new.values()),
            'inconsistent table column lengths: ' + name)
    passive_legacy = name == 'readout_events' and mode == 'legacy' and variant == 'enabled'
    if passive_legacy:
        require(old_n == 0 and new_n == events and np.array_equal(new['event_id'], np.arange(events)),
                'new passive legacy readout coverage differs')
        for key in old:
            require(old[key].dtype == new[key].dtype, 'passive legacy readout dtype differs: ' + key)
        for key, value in new.items():
            require(np.isfinite(value).all() and (value >= 0).all() and np.equal(value, np.floor(value)).all(),
                    'invalid passive legacy readout counter: ' + key)
        require((new['no_rindex_steps'] == 0).all(), 'passive legacy readout has NoRINDEX')
    else:
        require(old_n == new_n, 'original table row count differs: ' + name)
        for key in old:
            exact_array(old[key], new[key], name + '/' + key)
    if name == 'scan':
        require(old_n == new_n == events and np.array_equal(new['event_id'], np.arange(events)), 'scan event coverage differs')
    return {'name': name, 'original_rows': old_n, 'current_rows': new_n,
            'original_columns': sorted(old), 'zero_added_columns': sorted(extras),
            'allowed_passive_legacy_rows': passive_legacy}


def tree_arrays(tree):
    import numpy as np
    value = tree.arrays(library='np')
    return {key: value[key] for key in (value.dtype.names if isinstance(value, np.ndarray) else value)}


def compare_root_files(reference: Path, current: Path, *, variant: str, mode: str) -> dict:
    import uproot
    with uproot.open(reference) as old, uproot.open(current) as new:
        old_classes, new_classes = old.classnames(cycle=False), new.classnames(cycle=False)
        old_hist = {key: value for key, value in old_classes.items() if value.startswith('TH')}
        new_hist = {key: value for key, value in new_classes.items() if value.startswith('TH')}
        require(old_hist == new_hist, 'histogram set or classes differ')
        for name in old_hist:
            left, right = old[name], new[name]
            for getter in ('values', 'variances'):
                a, b = getattr(left, getter)(flow=True), getattr(right, getter)(flow=True)
                if a is None or b is None:
                    require(a is None and b is None, 'histogram missing variance differs: ' + name)
                else:
                    exact_array(a, b, name + '/' + getter)
            require(len(left.axes) == len(right.axes), 'histogram dimension differs: ' + name)
            for index, (a, b) in enumerate(zip(left.axes, right.axes)):
                exact_array(a.edges(flow=True), b.edges(flow=True), name + '/axis' + str(index))
            for field in ('fEntries', 'fTsumw', 'fTsumw2', 'fTsumwx', 'fTsumwx2', 'fTsumwy', 'fTsumwy2',
                          'fTsumwxy', 'fTsumwz', 'fTsumwz2', 'fTsumwxz', 'fTsumwyz', 'fMinimum', 'fMaximum',
                          'fNormFactor', 'fNcells', 'fStatOverflows'):
                if field in left.all_members:
                    require(field in right.all_members, 'histogram statistic missing: ' + name + '/' + field)
                    exact_array(left.member(field), right.member(field), name + '/' + field)
        is_table = lambda cls: cls == 'TTree' or 'RNTuple' in cls
        old_tables = {key for key, cls in old_classes.items() if is_table(cls)}
        new_tables = {key for key, cls in new_classes.items() if is_table(cls)}
        require(old_tables == new_tables and 'scan' in old_tables and 'readout_events' in old_tables,
                'original ROOT table set differs')
        checks = [compare_table_arrays(name, tree_arrays(old[name]), tree_arrays(new[name]), variant=variant, mode=mode)
                  for name in sorted(old_tables)]
    return {'tables': checks, 'histograms': sorted(old_hist), 'histogram_check': 'exact bins, variances, axes, and statistics'}


def audit(campaign: Path, reference: Path, executable: Path, output: Path | None = None) -> dict:
    campaign, reference, executable = campaign.resolve(), reference.resolve(), executable.resolve()
    if output is not None:
        output = output.resolve()
        require(not output.exists(), 'refusing to overwrite regression audit')
    manifest = validate_inputs(campaign)
    files, _, sources = reference_inventory(reference)
    require(files == manifest['reference_files_sha256'] and sources == manifest['reference_source_files_sha256'],
            'reference evidence changed since preparation')
    executable_hash, manifest_hash = digest(executable), digest(campaign / 'manifest.json')
    execution = read_json(campaign / 'execution.json')
    require(execution['schema_version'] == SCHEMA and execution['manifest_sha256'] == manifest_hash and
            execution['executable_sha256'] == executable_hash and execution['run_manager'] == 'Serial',
            'execution identity mismatch')
    tasks = []
    for task in manifest['tasks']:
        folder = campaign / task['id']
        original = reference / task['reference_task']
        receipt = read_json(folder / 'receipt.json')
        require(receipt['schema_version'] == SCHEMA and receipt['status'] == 'passed' and receipt['exit_code'] == 0 and
                not receipt['error'] and not receipt['ui_failure'] and not receipt['missing_or_empty_outputs'],
                'regression process did not pass: ' + task['id'])
        require(receipt['task_id'] == task['id'] and receipt['mode'] == task['mode'] and
                receipt['variant'] == task['variant'] and receipt['events_requested'] == 10 and
                receipt['seeds'] == task['seeds'] and receipt['run_manager'] == 'Serial', 'task receipt contract mismatch')
        for field, value in [('manifest_sha256', manifest_hash), ('executable_sha256', executable_hash),
                             ('execution_sha256', digest(campaign / 'execution.json')),
                             ('macro_sha256', digest(folder / 'run.mac')), ('log_sha256', digest(folder / 'simulation.log'))]:
            require(receipt[field] == value, 'receipt hash mismatch: ' + field)
        require(not log_failures((folder / 'simulation.log').read_text(errors='replace'), task), 'runtime log failed regression checks')
        for name in ('result_summary.csv', 'currentEvent.rndm', 'currentRun.rndm'):
            require((folder / name).read_bytes() == (original / name).read_bytes(), 'regression byte mismatch: ' + task['id'] + '/' + name)
        root_checks = compare_root_files(original / 'result.root', folder / 'result.root',
                                         variant=task['variant'], mode=task['mode'])
        tasks.append({'task_id': task['id'], 'reference_task': task['reference_task'], 'mode': task['mode'],
                      'variant': task['variant'], 'events': 10, 'status': 'passed', 'root_comparison': root_checks,
                      'files_sha256': {name: digest(folder / name) for name in OUTPUT_FILES}})
    report = {'schema_version': SCHEMA, 'phase': 'side2-regression', 'status': 'passed', 'task_count': 20,
              'engineering_events': 200, 'scientific_events': 0, 'executable_sha256': executable_hash,
              'manifest_sha256': manifest_hash, 'execution_sha256': digest(campaign / 'execution.json'),
              'reference_files_sha256': files, 'tasks': tasks,
              'comparison': 'exact original table columns, summary bytes, histogram numerics, and final RNG bytes; only enabled legacy readout gains passive rows'}
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--reference', required=True, type=Path)
    prep.add_argument('--output', required=True, type=Path)
    execute = sub.add_parser('run')
    execute.add_argument('--campaign', required=True, type=Path)
    execute.add_argument('--exe', required=True, type=Path)
    execute.add_argument('--timeout-seconds', type=float, default=900)
    check = sub.add_parser('audit')
    check.add_argument('--campaign', required=True, type=Path)
    check.add_argument('--reference', required=True, type=Path)
    check.add_argument('--exe', required=True, type=Path)
    check.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        manifest = prepare(args.reference, args.output)
        print(f"Prepared {len(manifest['tasks'])} regression processes; 200 engineering beta events, zero scientific events.")
    elif args.action == 'run':
        run(args.campaign, args.exe, args.timeout_seconds)
    else:
        report = audit(args.campaign, args.reference, args.exe, args.output)
        print(f"Regression {report['status']}: {report['task_count']} tasks, {report['engineering_events']} engineering events.")


if __name__ == '__main__':
    main()
