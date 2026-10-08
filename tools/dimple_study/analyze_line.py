#!/usr/bin/env python3
"""Read the sealed line scan, retain every event, and bootstrap independent cells.

This is exploratory post-scan analysis. No simulation or data replacement.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys

import numpy as np
import uproot

POSITIONS = (-40, -10, -5, -3.2, -2.8, -1.5, 0, 1.5, 2.8, 3.2, 5, 10, 40)
GEOMETRIES = ("flat", "dimple")
SEED = 2026100801
REPLICATES = 100_000
BLOCK_REPLICATES = 20_000
MEAN_METRICS = ("mean_D", "mean_G", "mean_optical_parent_births", "mean_all_births",
                "mean_electron_edep_mev", "mean_tile_edep_mev")
METRICS = (*MEAN_METRICS, "legacy_D_over_G", "entry_per_all_births", "optical_parent_fraction")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message: str) -> None:
    if not condition:
        raise ValueError(message)


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def table(tree) -> dict:
    values = tree.arrays(library="np")
    return values if isinstance(values, dict) else {n: values[n] for n in values.dtype.names}


def finite_or_none(value):
    return float(value) if np.isfinite(value) else None


def ratio(numerator, denominator):
    numerator, denominator = np.broadcast_arrays(numerator, denominator)
    return np.divide(numerator, denominator, out=np.full(numerator.shape, np.nan), where=denominator != 0)


def interval(estimate, replicates, adjusted=False) -> dict:
    invalid = int(np.count_nonzero(~np.isfinite(replicates)))
    result = {"estimate": finite_or_none(estimate), "invalid_bootstrap": invalid,
              "lo95": None, "hi95": None}
    if not invalid:
        result["lo95"], result["hi95"] = np.quantile(replicates, [.025, .975]).tolist()
    if adjusted:
        result.update(lo_adjusted=None, hi_adjusted=None)
        if not invalid:
            result["lo_adjusted"], result["hi_adjusted"] = np.quantile(
                replicates, [.05/(2*13), 1-.05/(2*13)]).tolist()
    return result


def derived(means: np.ndarray) -> dict:
    values = {name: means[..., i] for i, name in enumerate(MEAN_METRICS)}
    values["legacy_D_over_G"] = ratio(values["mean_D"], values["mean_G"])
    values["entry_per_all_births"] = ratio(values["mean_D"], values["mean_all_births"])
    values["optical_parent_fraction"] = ratio(values["mean_optical_parent_births"], values["mean_all_births"])
    return values


def bootstrap_means(values: np.ndarray, rng, count: int) -> np.ndarray:
    n = len(values)
    samples = np.empty((count, values.shape[1]))
    for start in range(0, count, 1000):
        stop = min(start+1000, count)
        indices = rng.integers(0, n, size=(stop-start, n))
        # The same sampled event indices apply to every observable.
        samples[start:stop] = values[indices].mean(axis=1)
    return samples


def load_campaign(batch: Path, expected_manifest: str):
    require(sha(batch / "manifest.json") == expected_manifest, "Manifest checksum mismatch")
    manifest = json.loads((batch / "manifest.json").read_text())
    verification = json.loads((batch / "verification.json").read_text())
    require(verification["status"] == "passed" and verification["accepted_tasks"] == 52
            and verification["accepted_events"] == 5200, "Incomplete accepted campaign")
    require(verification["manifest_sha256"] == expected_manifest, "Wrong verified manifest")
    require(sha(batch / "line-summary.csv") == verification["line_summary_sha256"], "Summary changed")
    require(not (batch / "STOP.json").exists(), "Campaign has a STOP record")
    expected = {(g, x, b) for g in GEOMETRIES for x in POSITIONS for b in range(2)}
    tasks = manifest["tasks"]
    require(len(tasks) == 52 and {(c['geometry'], c['x_mm'], c['block']) for c in tasks} == expected,
            "Task matrix differs from the registered line")
    require(len({c['name'] for c in tasks}) == 52, "Duplicate task ID")
    seeds = [s for c in tasks for s in c['seeds']]
    require(len(seeds) == len(set(seeds)) == 104, "Duplicate simulation seeds")
    require(not set(seeds) & set(manifest['excluded_engineering_seed_values']), "Reused engineering seeds")
    reports = {r['task']: r for r in verification['reports']}
    require(len(reports) == 52 and set(reports) == {c['name'] for c in tasks}, "Accepted report mismatch")
    require({p.name for p in (batch/'tasks').iterdir() if p.is_dir()} == set(reports), "Extra/missing task folders")
    require(sha(batch/'runtime/OpNovice2') == manifest['executable_sha256'], "Executable changed")
    for p, h in manifest['source_sha256'].items():
        require(sha(batch/'simulation-source'/p) == h, f"Source record changed: {p}")
    for p, h in manifest['tool_sha256'].items():
        require(sha(batch/'runtime'/p) == h, f"Runtime record changed: {p}")
    cells = {(g, x): [] for g in GEOMETRIES for x in POSITIONS}
    inventory, event_rows = [], []
    for case in tasks:
        directory = batch/'tasks'/case['name']
        audit = json.loads((directory/'audit.json').read_text())
        receipt = json.loads((directory/'receipt.json').read_text())
        require(audit == reports[case['name']] and audit['status'] == 'passed', "Audit/report mismatch")
        require(receipt['exit_code'] == 0 and receipt['executable_sha256'] == manifest['executable_sha256'],
                "Bad process/binary receipt")
        hashes = {name: sha(directory/name) for name in
                  ('result.root','result_summary.csv','simulation.log','result.rng','run.mac')}
        for filename, field in (('result.root','root_sha256'),('result_summary.csv','summary_sha256'),
                                ('simulation.log','log_sha256'),('result.rng','rng_state_sha256')):
            require(hashes[filename] == audit[field], f"Changed input: {case['name']}/{filename}")
        require(hashes['run.mac'] == case['macro_sha256'] == receipt['macro_sha256'], "Macro changed")
        with uproot.open(directory/'result.root') as source:
            scan, diag = table(source['scan']), table(source['dimple_optical_event_v1'])
        for name, values in (('scan',scan),('diagnostics',diag)):
            order = np.argsort(values['event_id'])
            require(np.array_equal(values['event_id'][order], np.arange(100)), f"Bad event IDs in {name}")
            for key in values:
                values[key] = values[key][order]
        d, g = scan['sipm_detected_photons'], scan['generated_optical_photons']
        optical = diag['optical_parent_scintillation_births']
        born = diag['all_optical_births']
        require(np.array_equal(g, diag['nonoptical_parent_scintillation_births']), "Wrong legacy G meaning")
        require(np.array_equal(g.astype(np.int64)+optical, born), "Birth partition mismatch")
        require(np.array_equal(born, diag['optical_tracks_started']), "Birth/start mismatch")
        require(np.array_equal(d, diag['sipm_unique_detected_tracks']), "Detection mismatch")
        for key, value in (('shoot_x_mm',case['x_mm']),('shoot_y_mm',0),('shoot_z_mm',4),
                            ('primary_kinetic_energy_mev',1)):
            require(np.allclose(scan[key], value, rtol=0, atol=1e-12), f"Wrong source {key}")
        for key, value in (('primary_count',1),('primary_pdg',11),('primary_dir_x',0),
                            ('primary_dir_y',0),('primary_dir_z',-1),('event_aborted',0)):
            require(np.allclose(diag[key], value, rtol=0, atol=1e-12), f"Wrong primary/aborted: {key}")
        values = np.column_stack((d,g,optical,born,scan['electron_tile_edep_mev'],scan['tile_edep_mev'])).astype(float)
        require(np.isfinite(values).all() and (values >= 0).all(), "Invalid observable values")
        require(int(g.sum()) == audit['generated_photons'] and int(d.sum()) == audit['detected_photons'],
                "Raw event totals differ from accepted audit")
        cells[(case['geometry'], case['x_mm'])].append((case['block'], values, scan))
        inventory.append({'task':case['name'],'hashes':hashes,'events':100})
        for i in range(100):
            event_rows.append({'task':case['name'],'event_id':i,'geometry':case['geometry'],
                'x_mm':case['x_mm'],'block':case['block'],'D':int(d[i]),'G':int(g[i]),
                'optical_parent_births':int(optical[i]),'all_births':int(born[i]),
                'electron_edep_mev':float(values[i,4]),'tile_edep_mev':float(values[i,5])})
    require(len({(r['task'],r['event_id']) for r in event_rows}) == 5200, "Event identity collision")
    return manifest, verification, cells, inventory, event_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def analyze(batch: Path, output: Path, expected_manifest: str) -> None:
    require(not output.exists(), "Use a new analysis directory; previous artifacts are retained")
    manifest, verification, cells, inventory, events = load_campaign(batch, expected_manifest)
    output.mkdir(parents=True)
    shutil.copy2(__file__, output/'analyze_line.py')
    write_csv(output/'events.csv', events)
    save(output/'input-inventory.json', {'manifest_sha256':expected_manifest,'tasks':inventory})
    seed_children = np.random.SeedSequence(SEED).spawn(52)
    configurations, boot, point_values, by_key = [], {}, {}, {}
    for gi, geometry in enumerate(GEOMETRIES):
        for pi, x in enumerate(POSITIONS):
            index = gi*13+pi
            group = sorted(cells[(geometry,x)], key=lambda t:t[0])
            require([g[0] for g in group] == [0,1], "Missing seed block")
            values = np.vstack([g[1] for g in group])
            require(len(values) == 200, "Wrong cell event count")
            estimates = derived(values.mean(axis=0))
            samples = derived(bootstrap_means(values,np.random.default_rng(seed_children[index]),REPLICATES))
            key = f'{geometry}_p{pi:02d}'
            for metric in METRICS:
                boot[f'{key}__{metric}'] = samples[metric]
            point_values[(geometry,x)] = estimates
            by_key[(geometry,x)] = key
            blocks = []
            for block, bv, _ in group:
                bm = derived(bv.mean(axis=0))
                blocks.append({'block':block,'n':100, **{n:finite_or_none(bm[n]) for n in
                               ('mean_D','mean_G','legacy_D_over_G')}})
            block_rng = np.random.default_rng(seed_children[26+index])
            block_delta = (bootstrap_means(group[1][1][:,:1],block_rng,BLOCK_REPLICATES)[:,0]
                           - bootstrap_means(group[0][1][:,:1],block_rng,BLOCK_REPLICATES)[:,0])
            boot[f'{key}__block_difference_D'] = block_delta
            d, g = values[:,0], values[:,1]
            largest_count = max(1,int(np.ceil(.01*len(d))))
            scan = {name:np.concatenate([b[2][name] for b in group]) for name in
                    ('hit_valid','hit_z_mm','scint_centroid_valid','scint_centroid_z_mm')}
            centroid_valid = scan['scint_centroid_valid'].astype(bool)
            valid_hit_z = scan['hit_z_mm'][scan['hit_valid'].astype(bool)]
            diagnostics = {'D_sd':float(d.std(ddof=1)), 'zero_D_fraction':float(np.mean(d==0)),
                'zero_G_fraction':float(np.mean(g==0)), 'D_median':float(np.median(d)),
                'D_p90':float(np.quantile(d,.9)), 'D_p99':float(np.quantile(d,.99)),
                'D_max':float(d.max()),
                'largest_event_fraction_of_sum_D':finite_or_none(ratio(d.max(),d.sum())),
                'top_one_percent_fraction_of_sum_D':finite_or_none(ratio(np.sort(d)[-largest_count:].sum(),d.sum())),
                'hit_valid_events':int(scan['hit_valid'].sum()),
                'hit_z_range_mm': [float(valid_hit_z.min()), float(valid_hit_z.max())]
                    if len(valid_hit_z) else None,
                'scint_centroid_valid_events':int(centroid_valid.sum()),
                'mean_event_legacy_scint_centroid_z_mm':float(scan['scint_centroid_z_mm'][centroid_valid].mean())
                    if centroid_valid.any() else None}
            configurations.append({'key':key,'geometry':geometry,'x_mm':x,'n':200,
                'metrics':{metric:interval(estimates[metric],samples[metric]) for metric in METRICS},
                'blocks':blocks,'block_difference_D':interval(blocks[1]['mean_D']-blocks[0]['mean_D'],block_delta),
                'diagnostics':diagnostics,
                'straight_incident_material_thickness_mm':5-np.sqrt(9-x*x) if geometry=='dimple' and abs(x)<3 else 5.})
            print(f'Analyzed {geometry} x={x}: 200 events, 100000 resamples',flush=True)
    contrasts = []
    for pi,x in enumerate(POSITIONS):
        f,d = point_values[('flat',x)],point_values[('dimple',x)]
        fb={m:boot[f'{by_key[("flat",x)]}__{m}'] for m in METRICS}
        db={m:boot[f'{by_key[("dimple",x)]}__{m}'] for m in METRICS}
        contrasts_data={'delta_D':(d['mean_D']-f['mean_D'],db['mean_D']-fb['mean_D'])}
        for name, metric in (('relative_D','mean_D'),('relative_G','mean_G'),
                             ('relative_legacy_ratio','legacy_D_over_G'),
                             ('relative_electron_edep','mean_electron_edep_mev')):
            contrasts_data[name]=(ratio(d[metric],f[metric])-1,ratio(db[metric],fb[metric])-1)
        components = [float(contrasts_data[m][0]) for m in
                      ('relative_D','relative_G','relative_legacy_ratio')]
        if np.isfinite(components).all():
            require(np.isclose(1+components[0], (1+components[1])*(1+components[2]),
                               rtol=1e-12), "Ratio decomposition failed")
        for metric,(_,samples) in contrasts_data.items():
            boot[f'contrast_p{pi:02d}__{metric}']=samples
        contrasts.append({'x_mm':x,'metrics':{metric:interval(estimate,samples,metric=='delta_D')
                          for metric,(estimate,samples) in contrasts_data.items()}})
    symmetry=[]
    for geometry in GEOMETRIES:
        for x in (1.5,2.8,3.2,5,10,40):
            left,right=point_values[(geometry,-x)],point_values[(geometry,x)]
            difference=boot[f'{by_key[(geometry,x)]}__mean_D']-boot[f'{by_key[(geometry,-x)]}__mean_D']
            symmetry.append({'geometry':geometry,'abs_x_mm':x,
                             'positive_minus_negative_D':interval(right['mean_D']-left['mean_D'],difference)})
    np.savez_compressed(output/'bootstrap.npz',**boot)
    csv_rows=[]
    for c in configurations:
        row={k:c[k] for k in ('geometry','x_mm','n')}
        for metric,summary in c['metrics'].items():
            row.update({f'{metric}_{suffix}':summary[suffix] for suffix in ('estimate','lo95','hi95')})
        csv_rows.append(row)
    write_csv(output/'configuration-summary.csv',csv_rows)
    contrast_rows=[]
    for c in contrasts:
        row={'x_mm':c['x_mm']}
        for metric,summary in c['metrics'].items():
            row.update({f'{metric}_{suffix}':v for suffix,v in summary.items()})
        contrast_rows.append(row)
    write_csv(output/'contrasts.csv',contrast_rows)
    result={'schema':'dimple-line-analysis-v1',
        'source':{'batch':str(batch),'manifest_sha256':expected_manifest,
                  'verification_sha256':sha(batch/'verification.json'),
                  'simulation_commit':manifest['simulation_commit'],'tool_commit':manifest['tool_commit'],
                  'input_tasks':52,'input_events':5200,'geometries':2,'positions':13},
        'method':{'timing':'Exploratory analysis specified after scan; not preregistered confirmatory inference',
                  'observation':'one incident electron; unique identity (task,event_id)',
                  'bootstrap_replicates':REPLICATES,'bootstrap_seed':SEED,'block_bootstrap_replicates':BLOCK_REPLICATES,
                  'rng':'NumPy default_rng(SeedSequence(seed).spawn(52)); first 26 children for cells, last 26 for block diagnostics',
                  'cell_order':'flat then dimple; increasing x',
                  'confidence':.95,'interval_method':'whole-event nonparametric percentile bootstrap; pooled 200 events/cell',
                  'joint_resampling':'D, G, all births, optical-parent births and energy use identical event indices within each cell',
                  'geometry_comparison':'independent samples; no matched event or seed pairing',
                  'adjusted_confidence':1-.05/13,'adjusted_family':'13 delta_D comparisons only; nominal 95% Bonferroni family-wise target',
                  'adjusted_quantiles':[.05/26,1-.05/26],
                  'invalid_ratio_policy':'undefined point estimates become null; any invalid replicate makes interval null; no dropping/retry',
                  'caveat':'Bootstrap coverage is approximate. Relative, auxiliary, block and symmetry intervals are descriptive pointwise 95% only.'},
        'software':{'python':platform.python_version(),'numpy':np.__version__,'uproot':uproot.__version__},
        'analysis_script_sha256':sha(Path(__file__)), 'input_inventory_sha256':sha(output/'input-inventory.json'),
        'bootstrap_sha256':sha(output/'bootstrap.npz'),
        'configurations':configurations,'contrasts':contrasts,'symmetry':symmetry}
    save(output/'result.json',result)
    print(f'Analysis saved to {output}',flush=True)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--manifest-sha256',required=True)
    args=parser.parse_args()
    analyze(args.batch.resolve(),args.output.resolve(),args.manifest_sha256)


if __name__ == '__main__':
    main()
