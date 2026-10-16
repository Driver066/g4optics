#!/usr/bin/env python3
"""Create an exploration qualification only by verifying actual v2 gate evidence.

This module is deliberately separate from the five frozen core tools. It never
runs simulation and never manufactures a passed gate from manually entered flags.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import audit as study_audit
import invalid_configs
import probes
from common import command_lines, digest, read_json, require, write_json

SCHEMA = 'combined-tiles-qualification-v2'
BASELINE_REGRESSION = 'exact scan columns, summary bytes, original histogram numerics, and final RNG bytes'


def safe_child(parent: Path, relative: str) -> Path:
    parent = parent.resolve()
    child = (parent / relative).resolve()
    require(child != parent and parent in child.parents, 'evidence path escapes its directory')
    return child


def verify_sources(repo: Path, sources: dict):
    require(bool(sources), 'missing source hash inventory')
    for relative, expected in sources.items():
        require(digest(safe_child(repo, relative)) == expected, f'current source hash mismatch: {relative}')


def check_report_binding(report: Path, campaign: Path, repo: Path, executable: Path,
                         phase: str, readout_mode: str, expected_sources: dict | None = None) -> tuple[dict, dict]:
    saved, manifest = read_json(report), read_json(campaign / 'manifest.json')
    require(saved.get('status') == 'passed', f'{phase} report has not passed')
    require(manifest['phase'] == phase, f'wrong {phase} campaign phase')
    # Probe reports intentionally omit phase/source inventory; its manifest is authoritative.
    require('phase' not in saved or saved['phase'] == phase, f'wrong {phase} report phase')
    require(saved['manifest_sha256'] == digest(campaign / 'manifest.json'), f'{phase} audit manifest hash mismatch')
    require(saved.get('readout_mode') == manifest['configuration'].get('readout_mode') == readout_mode,
            f'{phase} readout mode mismatch: expected {readout_mode}')
    require(saved['executable_sha256'] == digest(executable), f'{phase} executable hash mismatch')
    sources = manifest['source_files_sha256']
    verify_sources(repo, sources)
    if 'source_files_sha256' in saved:
        require(saved['source_files_sha256'] == sources, f'{phase} audit source inventory differs')
    if expected_sources is not None:
        core_sources = dict(sources)
        if phase == 'probe':
            require('tools/combined_tiles/probes.py' in core_sources, 'probe tool is not frozen')
            core_sources.pop('tools/combined_tiles/probes.py')
        require(core_sources == expected_sources, f'{phase} source inventory differs from corrected smoke')
    return saved, manifest


def verify_execution(campaign: Path, saved: dict, manifest: dict, executable: Path,
                     *, qualifier_report: Path | None = None) -> dict:
    """Bind every task receipt and audited artifact, including retained RNG states."""
    exe_hash = digest(executable)
    execution_path = campaign / 'execution.json'
    execution = read_json(execution_path)
    mode = manifest['configuration']['readout_mode']
    require(execution['manifest_sha256'] == digest(campaign / 'manifest.json'), 'execution manifest mismatch')
    require(execution['executable_sha256'] == exe_hash, 'execution executable mismatch')
    require(execution['readout_mode'] == mode and execution['run_manager'] == 'Serial', 'execution readout/runtime mismatch')
    if qualifier_report is not None:
        require(execution['qualification']['sha256'] == digest(qualifier_report), 'execution uses another qualifying audit')
    records = {row['task_id']: row for row in saved['tasks']}
    require(len(records) == len(saved['tasks']) == len(manifest['tasks']), 'duplicate/missing audit task')
    require(set(records) == {t['id'] for t in manifest['tasks']}, 'audit task matrix mismatch')
    task_evidence = []
    for task in manifest['tasks']:
        folder = safe_child(campaign, task['id'])
        receipt = read_json(folder / 'receipt.json')
        require(receipt['status'] == 'passed' and receipt['exit_code'] == 0, 'task receipt did not pass')
        require(not receipt['ui_failure'] and not receipt['missing_or_empty_outputs'], 'task UI/output failure retained')
        require(receipt['readout_mode'] == mode, 'task readout mode mismatch')
        require(receipt['executable_sha256'] == exe_hash, 'task executable hash mismatch')
        require(receipt['manifest_sha256'] == digest(campaign / 'manifest.json'), 'task manifest hash mismatch')
        require(receipt['execution_sha256'] == digest(execution_path), 'task execution receipt hash mismatch')
        require(receipt['macro_sha256'] == task['macro_sha256'] == digest(folder / 'run.mac'), 'task macro hash mismatch')
        require(receipt['log_sha256'] == digest(folder / 'simulation.log'), 'task log hash mismatch')
        require(receipt['events_requested'] == task['events'] and receipt['seeds'] == task['seeds'], 'task event/seed plan differs')
        if qualifier_report is not None:
            require(receipt['qualification']['sha256'] == digest(qualifier_report), 'task uses another qualifying audit')
        files = records[task['id']]['files_sha256']
        required = {'run.mac', 'simulation.log', 'receipt.json'}
        if task['events']:
            required |= {'result.root', 'result_summary.csv'}
        require(required <= set(files), 'audit omitted required task artifacts')
        for relative, expected in files.items():
            require(digest(safe_child(folder, relative)) == expected, 'audited task artifact changed: ' + relative)
        retained = dict(files)
        for rng in sorted(folder.glob('*.rndm')):
            retained[rng.name] = digest(rng)
        task_evidence.append({'id': task['id'], 'path': str(folder), 'files_sha256': retained})
    return {'audit_path': None, 'campaign_path': str(campaign),
            'manifest_sha256': digest(campaign / 'manifest.json'),
            'execution_sha256': digest(execution_path), 'readout_mode': mode, 'tasks': task_evidence}


def verify_base_smoke(manifest: dict, smoke_candidates: list[tuple[Path, dict]]) -> tuple[Path, dict]:
    matches = [(path, value) for path, value in smoke_candidates
               if manifest['base_smoke']['manifest_sha256'] == digest(path / 'manifest.json')]
    require(len(matches) == 1, 'diagnostic campaign is not derived from a supplied smoke manifest')
    path, base = matches[0]
    task_id = manifest['base_smoke']['task_id']
    tasks = [t for t in base['tasks'] if t['id'] == task_id and t['case_id'] == 'joined8-8']
    require(len(tasks) == 1, 'diagnostic base is not the joined8-8 smoke task')
    require(manifest['base_smoke']['macro_sha256'] == tasks[0]['macro_sha256'] == digest(safe_child(path, task_id) / 'run.mac'),
            'diagnostic source smoke macro differs')
    return path, base


def verify_invalid(report: Path, campaign: Path, repo: Path, executable: Path,
                   expected_sources: dict, smoke_candidates: list[tuple[Path, dict]]) -> dict:
    saved = read_json(report)
    manifest, tasks = invalid_configs.checked_tasks(campaign)
    require(saved.get('accepted') is True and saved['case_count'] == len(tasks) == 9, 'nine invalid cases have not all passed')
    require(saved['requested_physics_events'] == 0 and manifest['execution']['total_events'] == 0, 'invalid cases requested events')
    require(saved['manifest_sha256'] == digest(campaign / 'manifest.json'), 'invalid audit manifest hash mismatch')
    require(saved['executable_hashes'] == [digest(executable)], 'invalid audit executable differs')
    require(manifest['source_files_sha256'] == expected_sources, 'invalid source inventory differs')
    verify_sources(repo, manifest['source_files_sha256'])
    require(manifest['invalid_tool_sha256'] == digest(repo / 'tools/combined_tiles/invalid_configs.py'), 'invalid tool hash changed')
    source, smoke = verify_base_smoke(manifest, smoke_candidates)
    source_macro = (safe_child(source, manifest['base_smoke']['task_id']) / 'run.mac').read_text()
    expected_mode = smoke['configuration']['readout_mode']
    require(expected_mode in ('legacy', 'boundary-entry'), 'invalid base has unknown readout mode')
    cases = {row['task_id']: row for row in saved['cases']}
    require(len(cases) == len(saved['cases']) == 9 and set(cases) == {t['id'] for t, _ in tasks}, 'invalid audit case coverage')
    evidence = []
    for task, folder in tasks:
        row, receipt = cases[task['id']], read_json(folder / 'receipt.json')
        macro, log = folder / 'run.mac', folder / 'run.log'
        require(row['accepted'] is True and row['failures'] == [], 'invalid case not accepted')
        require(receipt['schema_version'] == invalid_configs.SCHEMA and receipt['task_id'] == task['id'], 'invalid receipt identity')
        require(not receipt['timed_out'] and not receipt['launch_error'], 'invalid process launch/timeout failure')
        require(receipt['requested_events'] == 0 and receipt['G4RUN_MANAGER_TYPE'] == 'Serial', 'invalid execution contract')
        require(receipt['executable_sha256'] == digest(executable), 'invalid receipt executable differs')
        require(receipt['manifest_sha256'] == digest(campaign / 'manifest.json'), 'invalid receipt manifest mismatch')
        require(receipt['macro_sha256'] == task['macro_sha256'] == digest(macro), 'invalid receipt macro mismatch')
        require(receipt['log_sha256'] == digest(log), 'invalid receipt log mismatch')
        require(row['returncode'] == receipt['returncode'], 'invalid return code differs')
        require(row['expected_code'] == task['expected_rejection']['code'] and
                row['expected_diagnostic'] == task['expected_rejection']['diagnostic'], 'invalid expected rejection differs')
        require(macro.read_text() == invalid_configs.make_macro(source_macro, task['expected_rejection']), 'invalid mutation contract differs')
        modes = [line for line in command_lines(macro.read_text()) if line.startswith('/opnovice2/sipm/readoutMode ')]
        require(modes == ['/opnovice2/sipm/readoutMode ' + expected_mode], 'invalid physical readout mode differs from its source')
        failures = invalid_configs.evaluate_log(log.read_text(errors='replace'), receipt['returncode'], task['expected_rejection'])
        require(not failures, 'invalid rejection did not qualify: ' + '; '.join(failures))
        require(not (folder / 'result.root').exists() and not (folder / 'result_summary.csv').exists(), 'invalid case produced physics outputs')
        evidence.append({'id': task['id'], 'path': str(folder), 'readout_mode': expected_mode,
                         'files_sha256': {name: digest(folder / name) for name in ('run.mac', 'run.log', 'receipt.json')}})
    return {'audit_path': str(report), 'audit_sha256': digest(report), 'campaign_path': str(campaign),
            'manifest_sha256': digest(campaign / 'manifest.json'), 'readout_mode': expected_mode,
            'accepted_cases': 9, 'tasks': evidence}


def qualify(reports: dict[str, Path], campaigns: dict[str, Path], repo: Path, executable: Path,
            output: Path, baseline_campaign: Path | None = None) -> dict:
    require(not output.exists(), 'qualification output already exists')
    repo, executable = repo.resolve(), executable.resolve()
    reports = {key: value.resolve() for key, value in reports.items()}
    campaigns = {key: value.resolve() for key, value in campaigns.items()}
    smoke_saved, smoke_manifest = check_report_binding(reports['smoke'], campaigns['smoke'], repo, executable,
                                                       'smoke', 'boundary-entry')
    sources = smoke_manifest['source_files_sha256']
    loaded = {'smoke': (smoke_saved, smoke_manifest)}
    for key, phase, mode in [('geometry', 'geometry', 'boundary-entry'), ('legacy_smoke', 'smoke', 'legacy'),
                             ('probes', 'probe', 'boundary-entry')]:
        loaded[key] = check_report_binding(reports[key], campaigns[key], repo, executable, phase, mode, sources)
    # Re-evaluate the existing raw data through the immutable original audit code.
    # These functions are read-only unless their CLI's --report option is used.
    for key in ('geometry', 'smoke', 'legacy_smoke'):
        fresh = study_audit.audit(campaigns[key], repo, executable,
                                  baseline=baseline_campaign if key == 'legacy_smoke' else None)
        require(fresh == loaded[key][0], f'{key} report does not match current raw evidence')
    legacy_rows = loaded['legacy_smoke'][0]['tasks']
    for case in ('mono16', 'mono24'):
        matches = [row for row in legacy_rows if row['case_id'] == case]
        require(len(matches) == 1 and matches[0].get('baseline_regression') == BASELINE_REGRESSION,
                f'{case} has not passed full historical legacy regression')
    probe_saved, probe_manifest = loaded['probes']
    verify_base_smoke(probe_manifest, [(campaigns['smoke'], smoke_manifest)])
    fresh_probe = probes.audit(campaigns['probes'], repo, executable)
    require(fresh_probe == probe_saved, 'probe report does not match current raw evidence')
    evidence = {}
    for key, (saved, manifest) in loaded.items():
        incoming = reports['geometry'] if key == 'smoke' else reports['smoke'] if key == 'probes' else None
        item = verify_execution(campaigns[key], saved, manifest, executable, qualifier_report=incoming)
        item.update(audit_path=str(reports[key]), audit_sha256=digest(reports[key]))
        evidence[key] = item
    evidence['invalid'] = verify_invalid(reports['invalid'], campaigns['invalid'], repo, executable, sources,
                                         [(campaigns['smoke'], smoke_manifest),
                                          (campaigns['legacy_smoke'], loaded['legacy_smoke'][1])])
    result = {'schema_version': SCHEMA, 'status': 'passed', 'smoke_status': 'passed',
              'physics_probes_status': 'passed', 'geometry_status': 'passed', 'legacy_regression_status': 'passed',
              'invalid_configuration_status': 'passed', 'readout_mode': 'boundary-entry',
              'source_files_sha256': sources, 'executable_sha256': digest(executable),
              'qualification_tool_sha256': digest(Path(__file__)),
              'scope': 'Fresh corrected-readout finite exploration only; rejected v1 outputs are not scientific evidence.',
              'evidence': evidence}
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    keys = ('geometry', 'smoke', 'probes', 'legacy-smoke', 'invalid')
    for key in keys:
        parser.add_argument('--' + key, type=Path, required=True, help=key + ' audit JSON file')
        parser.add_argument('--' + key + '-campaign', type=Path,
                            help='Actual campaign directory; default is audit parent / ' + key)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--executable', required=True, type=Path)
    parser.add_argument('--baseline-campaign', type=Path,
                        help='Actual old baseline directory after relocation; otherwise use legacy-smoke manifest path')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    reports = {key.replace('-', '_'): getattr(args, key.replace('-', '_')) for key in keys}
    campaigns = {key.replace('-', '_'): (getattr(args, key.replace('-', '_') + '_campaign') or
                                         reports[key.replace('-', '_')].parent / key) for key in keys}
    result = qualify(reports, campaigns, args.repo, args.executable, args.output, args.baseline_campaign)
    print(f"Qualified corrected boundary-entry exploration from {len(result['evidence'])} verified gate bundles: {args.output}")


if __name__ == '__main__':
    main()
