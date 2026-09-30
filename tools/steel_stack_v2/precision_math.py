"""Registered precision design; complete neutron events are the sampling unit."""
from __future__ import annotations

import hashlib
import math
import numpy as np

from model import require

SPEC = {
    "schema_version": "steel-gap-precision-v1",
    "metric": "all sensor detections per incident neutron, including zero responses",
    "pairs": [[4, "back-center"], [4, "edge-two"], [24, "back-center"], [24, "edge-two"]],
    "reference_gap_mm": 0.5, "candidate_gap_mm": 1.0,
    "target_half_width": 0.05, "family_confidence": 0.95, "comparison_count": 4,
    "individual_confidence": 0.9875, "corrected_quantiles": [0.00625, 0.99375],
    "pointwise_quantiles": [0.025, 0.975], "z": 2.4977054744,
    "resamples": 10000, "analysis_seed": 2026093002, "variance_quantile": 0.90,
    "sample_margin": 1.20, "minimum_main_events_per_configuration": 1000,
    "events_per_task": 100, "calibration_blocks": 10, "calibration_events": 8000,
    "calibration_seed": 2026093002, "main_seed": 2026093003,
    "bootstrap_unit": "complete neutron event within each independent task block",
    "calibration_in_main_estimate": False, "benchmark_in_main_estimate": False,
    "equivalence_tested": False, "submit_main": False,
    "resources": {"cpus_per_task": 1, "memory_gib": 4, "node_constraint": "40core",
                  "time_minutes": 60, "max_parallel": 4},
}


class PlanningError(ValueError):
    """Valid data may still fail to identify a usable precision design."""


def rng_for(task_id, seed=SPEC["analysis_seed"]):
    words = np.frombuffer(hashlib.sha256(("precision-v1:"+task_id).encode()).digest()[:16], dtype="<u4")
    return np.random.default_rng(np.random.SeedSequence([seed, *map(int, words)]))


def bootstrap_sums(blocks, replicas=SPEC["resamples"], chunk=256, seed=SPEC["analysis_seed"]):
    """One weight vector per event, shared by every field, with bounded memory."""
    require(blocks and len({b["task_id"] for b in blocks}) == len(blocks), "Empty/duplicate bootstrap blocks")
    require(type(chunk) is int and chunk > 0 and type(replicas) is int and replicas > 0, "Invalid bootstrap size")
    ordered = sorted(blocks, key=lambda b: (b["block"], b["task_id"]))
    columns = ordered[0]["values"].shape[1]
    result = np.zeros((replicas, columns), dtype=np.float64)
    for block in ordered:
        values = np.asarray(block["values"], dtype=np.float64)
        require(values.ndim == 2 and values.shape[1] == columns and len(values) > 1 and
                np.isfinite(values).all(), "Invalid event matrix")
        n = len(values)
        rng = rng_for(block["task_id"], seed)
        for start in range(0, replicas, chunk):
            stop = min(replicas, start+chunk)
            weights = rng.multinomial(n, np.full(n, 1/n), size=stop-start)
            # Each row uses the same reduction order regardless of chunk size.
            result[start:stop] += np.einsum("ij,jk->ik", weights, values, optimize=False)
    return result


def event_statistics(values):
    x = np.asarray(values, dtype=float)
    require(x.ndim == 1 and len(x) > 1 and np.isfinite(x).all() and (x >= 0).all(), "Invalid response events")
    mean = float(x.mean())
    variance = float(x.var(ddof=1))
    total = float(x.sum())
    order = np.sort(x)
    return dict(events=len(x), mean=mean, variance=variance, sd=math.sqrt(variance),
        cv=math.sqrt(variance)/mean if mean > 0 else None,
        zero_response_fraction=float(np.mean(x == 0)),
        largest_event_fraction=float(order[-1]/total) if total > 0 else None,
        largest_one_percent_fraction=float(order[-max(1, math.ceil(len(x)*.01)):].sum()/total) if total > 0 else None,
        maximum=float(order[-1]), p50=float(np.quantile(x, .5)), p95=float(np.quantile(x, .95)),
        p99=float(np.quantile(x, .99)), includes_zero_events=True)


def group_statistics(blocks, fields, replicas=SPEC["resamples"], chunk=256):
    ordered = sorted(blocks, key=lambda b: (b["block"], b["task_id"]))
    require(fields[0] == "detected" and len(set(fields)) == len(fields), "Response must be the first unique field")
    values = np.concatenate([b["values"] for b in ordered])
    response = values[:, 0]
    stats = event_statistics(response)
    center = stats["mean"]
    # Centered moments avoid cancellation when the response has a large offset.
    centered = [{**b, "values": np.column_stack((b["values"], b["values"][:, 0]-center,
                (b["values"][:, 0]-center)**2))} for b in ordered]
    sums = bootstrap_sums(centered, replicas=replicas, chunk=chunk)
    n = len(values)
    mean_boot = center+sums[:, -2]/n
    var_boot = (sums[:, -1]-sums[:, -2]**2/n)/(n-1)
    require((var_boot >= -64*np.finfo(float).eps*np.maximum(sums[:, -1], 1)).all(), "Invalid bootstrap variance")
    var_boot = np.maximum(var_boot, 0)
    auxiliary = {}
    for index, field in enumerate(fields):
        if field in ("detected", "generated_legacy", "births_tile", "detected_tile_birth"):
            continue
        auxiliary[field] = interval(float(values[:, index].mean()), sums[:, index]/n, [.025, .975])
    for name, numerator, denominator in (("legacy_ce", "detected", "generated_legacy"),
                                         ("birth_ce", "detected_tile_birth", "births_tile")):
        if numerator in fields and denominator in fields:
            i, j = fields.index(numerator), fields.index(denominator)
            ratio_boot = np.divide(sums[:, i], sums[:, j], out=np.full(replicas, np.nan), where=sums[:, j] > 0)
            denominator_sum = values[:, j].sum()
            point = values[:, i].sum()/denominator_sum if denominator_sum > 0 else float("nan")
            auxiliary[name] = interval(point, ratio_boot, [.025, .975])
    midpoint = len(ordered)//2
    halves = [event_statistics(np.concatenate([b["values"][:, 0] for b in part]))
              for part in (ordered[:midpoint], ordered[midpoint:])]
    return dict(statistics=stats, bootstrap_mean=mean_boot, bootstrap_variance=var_boot,
                bootstrap_sums=sums[:, :len(fields)], fields=fields, auxiliary=auxiliary,
                halves=halves, response=response,
                blocks=[dict(task_id=b["task_id"], block=b["block"], **event_statistics(b["values"][:, 0])) for b in ordered])


def interval(point, samples, quantiles):
    samples = np.asarray(samples, dtype=float)
    valid = bool(np.isfinite(point) and np.isfinite(samples).all())
    limits = np.quantile(samples, quantiles).tolist() if valid else [None, None]
    return dict(estimate=float(point) if np.isfinite(point) else None, valid=valid,
                low=limits[0], high=limits[1], invalid_resamples=int((~np.isfinite(samples)).sum()),
                resamples=len(samples), quantiles=list(quantiles),
                half_width=(limits[1]-limits[0])/2 if valid else None)


def variance_coefficient(mean0, variance0, mean1, variance1):
    m0, v0, m1, v1 = np.broadcast_arrays(mean0, variance0, mean1, variance1)
    if not (np.isfinite([m0, v0, m1, v1]).all() and (m0 > 0).all() and (m1 > 0).all() and
            (v0 >= 0).all() and (v1 >= 0).all()):
        raise PlanningError("Nonpositive reference/response or invalid variance; do not drop invalid resamples")
    result = v1/m0**2+m1**2*v0/m0**4
    if not (np.isfinite(result).all() and (result > 0).all()):
        raise PlanningError("Degenerate/nonfinite response variance; no executable main recommendation")
    return result


def required_events(a, half_width=SPEC["target_half_width"], margin=SPEC["sample_margin"]):
    require(all(math.isfinite(v) and v > 0 for v in (a, half_width, margin)), "Invalid sample-size inputs")
    return 100*math.ceil(max(1000, margin*SPEC["z"]**2*a/half_width**2)/100)


def compare_pair(reference, candidate, *, thickness, layout):
    a, b = reference["statistics"], candidate["statistics"]
    point_a = float(variance_coefficient(a["mean"], a["variance"], b["mean"], b["variance"]))
    boot_a = variance_coefficient(reference["bootstrap_mean"], reference["bootstrap_variance"],
                                  candidate["bootstrap_mean"], candidate["bootstrap_variance"])
    require(len(boot_a) == SPEC["resamples"], "Wrong registered resample count")
    upper = max(point_a, float(np.quantile(boot_a, .90)))
    delta = b["mean"]/a["mean"]-1
    delta_boot = candidate["bootstrap_mean"]/reference["bootstrap_mean"]-1
    corrected = interval(delta, delta_boot, SPEC["corrected_quantiles"])
    if not corrected["valid"]:
        raise PlanningError("Invalid ratio interval; conditional intervals are not accepted")
    halves = []
    for left, right in zip(reference["halves"], candidate["halves"]):
        try:
            coefficient = float(variance_coefficient(left["mean"], left["variance"], right["mean"], right["variance"]))
            halves.append(dict(valid=True, A=coefficient, point_events_per_arm=required_events(coefficient, margin=1),
                               reference=left, candidate=right))
        except PlanningError as error:
            halves.append(dict(valid=False, reason=str(error), reference=left, candidate=right))
    return dict(tile_thickness_mm=thickness, layout=layout, reference_gap_mm=.5, candidate_gap_mm=1.,
        calibration_delta=delta, pointwise95=interval(delta, delta_boot, [.025, .975]),
        corrected9875=corrected, A_hat=point_a, A_bootstrap_p90=float(np.quantile(boot_a, .90)), A_plus=upper,
        point_events_per_arm=required_events(point_a, margin=1),
        conservative_events_per_arm=required_events(upper), half_sample_checks=halves,
        calibration_only=True, main_precision_verified=False, bootstrap_delta=delta_boot, bootstrap_A=boot_a)
