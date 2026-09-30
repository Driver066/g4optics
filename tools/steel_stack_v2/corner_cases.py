"""Preregistered, deterministic rays in the complete ten-layer v2 scene."""
from __future__ import annotations
import itertools
import math
from seed_registry import seeds
from model import make_configuration

TOLERANCE_MM = 1.e-9
ENERGY_EV = 2.6988098789
SCALES = (0,16,32,64)
OFFSETS = (-32.,-4.,-1.,-.25,0.,.25,1.,4.,32.)
LAYOUTS = ("back-four","back-two","edge-two","back-center")

def unit(v):
    norm=math.sqrt(sum(x*x for x in v))
    return [x/norm for x in v]

def cross(a,b):
    return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]

def polarizations(direction):
    # Fixed reference; no random polarization or direction calls.
    axis=[1.,0.,0.] if abs(direction[0])<.9 else [0.,1.,0.]
    first=unit(cross(axis,direction))
    return [first,unit(cross(direction,first))]

def ray_cases():
    center_z=205.75 # tile 1, unchanged back-four t24 g0.5 geometry
    half=[50.,50.,12.]
    result=[]
    def ray(name,family,point,direction,layout="back-four",start=None,**metadata):
        direction=unit(direction)
        start=start if start is not None else [p-d for p,d in zip(point,direction)]
        result.append(dict(case_id=name,family=family,layout=layout,position_mm=start,
            intended_hit_mm=point,direction=direction,polarizations=polarizations(direction),
            energy_ev=ENERGY_EV,**metadata))
    direction=[.18967322049273222,.11096897764603809,.97555622873728221]
    ray("captured-edge","captured",[49.999999999728239,37.891445058502427,217.75],direction)
    for first,second in itertools.combinations(range(3),2):
        remaining=3-first-second
        for signs in itertools.product((-1,1),repeat=2):
            for offset in OFFSETS:
                point=[0.,0.,0.];direction=[0.,0.,0.]
                point[first]=signs[0]*(half[first]-offset*TOLERANCE_MM)
                point[second]=signs[1]*half[second]
                point[2]+=center_z
                direction[first]=signs[0]*.18967322049273222
                direction[second]=signs[1]*.97555622873728221
                direction[remaining]=.11096897764603809
                ray(f"edge-{first}{second}-{signs[0]:+d}{signs[1]:+d}-{offset:+g}","edge",point,direction,
                    axes=[first,second],signs=list(signs),offset_tau=offset)
    for signs in itertools.product((-1,1),repeat=3):
        for xoff,yoff in [(0.,0.),*itertools.product((-.25,.25),repeat=2)]:
            point=[signs[0]*(50.-xoff*TOLERANCE_MM),signs[1]*(50.-yoff*TOLERANCE_MM),center_z+signs[2]*12.]
            direction=[s*d for s,d in zip(signs,(.18967322049273222,.11096897764603809,.97555622873728221))]
            ray(f"vertex-{signs[0]:+d}{signs[1]:+d}{signs[2]:+d}-{xoff:+g}-{yoff:+g}","vertex",point,direction,
                signs=list(signs),tangent_offsets_tau=[xoff,yoff])
    for axis in range(3):
        for sign in (-1,1):
            point=[0.,0.,center_z];point[axis]+=sign*half[axis]
            direction=[0.,0.,0.];direction[axis]=sign
            ray(f"face-{axis}-{sign:+d}","control-face",point,direction)
    for layout in LAYOUTS:
        for edge in (False,True):
            if layout=="edge-two":
                point=[50.,-25.+(1.2 if edge else 0.),center_z];direction=[1.,0.,0.]
            else:
                u,v=(0.,0.) if layout=="back-center" else (-25.,-25.)
                point=[u+(1.2 if edge else 0.),v,center_z-12.];direction=[0.,0.,-1.]
            ray(f"sensor-{layout}-{'edge' if edge else 'center'}","control-sensor",point,direction,layout)
    # Bare-steel control deliberately has no tile-painted border. Its original
    # NoRINDEX is expected evidence, never exempted in the neutron production audit.
    ray("bare-steel","control-steel",[100.,0.,217.75],[0.,0.,1.])
    ray("world-only","control-world",[301.,0.,0.],[1.,0.,0.],start=[300.,0.,0.])
    assert len(result)==165 and len({v['case_id'] for v in result})==165
    return result

def inputs():
    rows=[]
    for case in ray_cases():
        for polarization in range(2):
            for block in range(8):
                pair=seeds(f"corner-v1-ray-block-{block}")
                rows.append(dict(case,probe_id=f"{case['case_id']}__p{polarization}__b{block}",
                    polarization_index=polarization,polarization=case['polarizations'][polarization],
                    block=block,seed1=pair[0],seed2=pair[1],paired_control=True,independent_science_sample=False))
    assert len(rows)==2640
    return rows

def table(rows):
    return "# id seed1 seed2 x_mm y_mm z_mm dx dy dz px py pz energy_eV\n"+"".join(
        " ".join([r['probe_id'],str(r['seed1']),str(r['seed2']),
                  *(format(v,'.17g') for v in (*r['position_mm'],*r['direction'],*r['polarization'],r['energy_ev']))])+"\n"
        for r in rows)

def config(layout,scale,profile="painted-corner-v1"):
    return make_configuration(layout,24,.5,"legacy" if scale==0 else profile,scale)
