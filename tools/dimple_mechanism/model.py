"""Fixed issue #9 engineering model; no production scan API."""
from __future__ import annotations

import math

TILE_MM = (100., 100., 24.)
RADIUS_MM = 3.
REMOVED_VOLUME_MM3 = 2 * math.pi * RADIUS_MM ** 3 / 3
CUBE_SIDE_MM = REMOVED_VOLUME_MM3 ** (1 / 3)
SOURCE_Z_MM = 18.
TEMPLATE = "test/OpNovice2/ej200_sipm_gps_test.mac"
CONFIGURATIONS = (
    dict(key="A", label="flat-small", enabled=False, shape="hemisphere", front=False, sensor_mm=2.),
    dict(key="B", label="hemisphere-small", enabled=True, shape="hemisphere", front=False, sensor_mm=2.),
    dict(key="C", label="flat-large", enabled=False, shape="hemisphere", front=False, sensor_mm=6.),
    dict(key="D", label="hemisphere-large", enabled=True, shape="hemisphere", front=False, sensor_mm=6.),
    dict(key="E", label="hemisphere-front-small", enabled=True, shape="hemisphere", front=True, sensor_mm=2.),
    dict(key="F", label="cube-front-small", enabled=True, shape="cube", front=True, sensor_mm=2.),
    dict(key="G", label="cube-small", enabled=True, shape="cube", front=False, sensor_mm=2.),
)


def validate_configuration(config: dict) -> None:
    expected = next((item for item in CONFIGURATIONS if item["key"] == config.get("key")), None)
    if expected is None or config != expected:
        raise ValueError("Configuration differs from the fixed approved seven-arm model")


def material_commands(template: str) -> str:
    keep = []
    for line in template.splitlines():
        active = line.partition("#")[0].strip()
        command = active.split()[0] if active else ""
        if command.startswith("/process/optical/") or command in {
            "/opnovice2/boxMaterial", "/opnovice2/worldMaterial",
            "/opnovice2/boxProperty", "/opnovice2/boxConstProperty",
            "/opnovice2/worldProperty", "/opnovice2/worldConstProperty",
            "/opnovice2/surfaceModel", "/opnovice2/surfaceType",
            "/opnovice2/surfaceFinish", "/opnovice2/surfaceSigmaAlpha"}:
            keep.append(active)
    required = ["/opnovice2/boxConstProperty SCINTILLATIONYIELD 10000",
                "/opnovice2/worldProperty ABSLENGTH 0.0000020 100 0.0000033 100",
                "/opnovice2/surfaceFinish polished"]
    if not all(item in keep for item in required):
        raise ValueError("Template does not preserve the approved historical optical model")
    return "\n".join(keep)


def cases() -> list[dict]:
    result = []
    for config in CONFIGURATIONS:
        result.append(dict(name=f"geometry-{config['key']}", stage="geometry", config=dict(config),
                           events=0, expected_rejection=None, overrides={}))
    negatives = (
        ("front-without-dimple", "A", {"/opnovice2/dimple/frontCompensation": "true"}, "OpNovice2_Issue9_007"),
        ("cube-opening", "G", {"/opnovice2/dimple/sipmMode": "opening"}, "OpNovice2_Issue9_001"),
        ("front-opening", "E", {"/opnovice2/dimple/sipmMode": "opening"}, "OpNovice2_Issue9_001"),
        ("external-grease", "B", {"/opnovice2/grease/enabled": "true"}, "OpNovice2_Dimple_015"),
        ("old-opening-large", "D", {"/opnovice2/dimple/sipmMode": "opening", "/opnovice2/diagnostics/issue9Geometry": "false"}, "OpNovice2_Dimple_008"),
        ("cube-too-deep", "G", {"/opnovice2/tank/size": "100 100 3 mm"}, "OpNovice2_Issue9_003"),
        ("front-outside-world", "F", {"/opnovice2/tank/size": "100 100 998 mm"}, "OpNovice2_Issue9_005"),
    )
    for name, key, overrides, rejection in negatives:
        config = next(dict(item) for item in CONFIGURATIONS if item["key"] == key)
        result.append(dict(name=f"reject-{name}", stage="geometry", config=config, events=0,
                           expected_rejection=rejection, overrides=overrides))
    for config in CONFIGURATIONS:
        for x in (0., 3.2, 10.):
            result.append(dict(name=f"smoke-{config['key']}-x{x:g}", stage="smoke", config=dict(config),
                               events=2, x_mm=x, diagnostics=True, expected_rejection=None, overrides={}))
    for index, case in enumerate(result):
        case["seeds"] = [1927101001 + index * 2, 1927101002 + index * 2]
    return result


def macro(template: str, case: dict) -> str:
    config = case["config"]
    validate_configuration(config)
    commands = {
        "/opnovice2/tank/size": "100 100 24 mm",
        "/opnovice2/tank/bottomCavity": "false", "/opnovice2/absorber/enabled": "false",
        "/opnovice2/stack/enabled": "false", "/opnovice2/stack/layoutStudy": "false",
        "/opnovice2/grease/enabled": "false", "/opnovice2/sipm/layout": "single",
        "/opnovice2/sipm/face": "-Z", "/opnovice2/sipm/localPosition": "0 0 0 mm",
        "/opnovice2/sipm/size": f"{config['sensor_mm']:g} {config['sensor_mm']:g} 0.5 mm",
        "/opnovice2/dimple/enabled": str(config["enabled"]).lower(),
        "/opnovice2/dimple/radius": "3 mm", "/opnovice2/dimple/mode": config["shape"],
        "/opnovice2/dimple/sipmMode": "external",
        "/opnovice2/dimple/frontCompensation": str(config["front"]).lower(),
        "/opnovice2/diagnostics/issue9Geometry": "true",
    }
    if case["stage"] not in ("geometry", "smoke"):
        raise ValueError("Only fixed engineering geometry/smoke stages are supported")
    if case["stage"] == "smoke" and case["events"] != 2:
        raise ValueError("Engineering smoke is fixed at two events per point")
    if case["stage"] == "geometry" and case["events"] != 0:
        raise ValueError("Geometry stage must contain zero events")
    commands.update(case.get("overrides", {}))
    seed1, seed2 = case["seeds"]
    text = ("# Issue #9 fixed engineering check; not a production scan.\n"
            "/control/verbose 0\n/run/verbose 0\n/tracking/verbose 0\n/tracking/storeTrajectory 0\n"
            f"/random/setSeeds {seed1} {seed2}\n" + material_commands(template) + "\n" +
            "\n".join(f"{key} {value}" for key, value in commands.items()) + "\n")
    if case["stage"] == "smoke":
        text += "/opnovice2/diagnostics/dimpleOptical true\n/opnovice2/diagnostics/saveRandomState true\n"
    text += "/run/initialize\n/geometry/test/resolution 10000\n/geometry/test/run\n"
    if case["stage"] == "smoke":
        text += ("/analysis/setFileName result\n/analysis/h1/set 2 100 2.0 3.5\n"
                 "/analysis/h1/set 3 400 0 100\n/analysis/h1/set 27 100 2.0 3.5\n"
                 "/analysis/h1/set 28 400 0 100\n/opnovice2/gun/electronEnergyMode fixed\n"
                 f"/gps/particle e-\n/gps/pos/type Point\n/gps/pos/centre {case['x_mm']:g} 0 18 mm\n"
                 "/gps/direction 0 0 -1\n/gps/energy 1.0 MeV\n"
                 f"/random/setSeeds {seed1} {seed2}\n/run/printProgress 2\n/run/beamOn 2\n")
    return text + f"/control/echo ISSUE9_{case['stage'].upper()}_DONE\n"
