#!/usr/bin/env python3
"""Qualify the 8000-beta comparison from revalidated geometry, smoke and probes."""
import argparse
from pathlib import Path
from study import *
import validation


def qualify(reports,campaigns,repo,executable,output,regression_reference=None):
    require(not Path(output).exists(),'qualification output exists')
    import probes
    import regression
    expected=configurations();evidence={};sources=None;exe_hash=digest(executable)
    for phase in ('geometry','smoke','probes'):
        report,campaign=Path(reports[phase]),Path(campaigns[phase]);saved=read_json(report)
        manifest=read_json(campaign/'manifest.json')
        require(saved['status']=='passed' and saved['manifest_sha256']==digest(campaign/'manifest.json'),'gate report is stale or failed')
        fresh=probes.audit(campaign,repo,executable) if phase=='probes' else validation.audit(campaign,repo,executable)
        require(saved==fresh,'gate raw evidence no longer matches saved audit: '+phase)
        core_sources={p:h for p,h in manifest['source_files_sha256'].items() if p!='tools/combined_layouts/probes.py'}
        if sources is None:sources=core_sources
        require(core_sources==sources,'gate source inventory mismatch')
        require(saved['executable_sha256']==exe_hash,'gate executable mismatch')
        if phase!='probes':require(manifest['configurations']==expected,'gate matrix mismatch')
        if phase=='smoke':
            require(read_json(campaign/'execution.json')['qualification']['sha256']==digest(reports['geometry']),'smoke qualified from another geometry audit')
        if phase=='probes':
            require(manifest['base_smoke']['manifest_sha256']==digest(Path(campaigns['smoke'])/'manifest.json'),'probes use another smoke source')
            require(read_json(campaign/'execution.json')['qualification']['sha256']==digest(reports['smoke']),'probes qualified from another smoke audit')
        files={'audit':{'path':str(report.resolve()),'sha256':digest(report)},
               'manifest':{'path':str((campaign/'manifest.json').resolve()),'sha256':digest(campaign/'manifest.json')},
               'execution':{'path':str((campaign/'execution.json').resolve()),'sha256':digest(campaign/'execution.json')}}
        evidence[phase]={'files':files,'task_artifacts':saved['tasks']}
    require(regression_reference is not None,'regression reference required')
    regression_report=Path(reports['regression']);saved=read_json(regression_report)
    fresh=regression.audit(Path(campaigns['regression']),Path(regression_reference),Path(executable),output=None)
    require(saved==fresh and saved['status']=='passed','regression evidence failed or changed')
    require(saved['executable_sha256']==exe_hash,'regression executable differs')
    evidence['regression']={'audit_path':str(regression_report.resolve()),'audit_sha256':digest(regression_report),
                            'campaign_path':str(Path(campaigns['regression']).resolve()),'report':saved}
    result={'schema_version':SCHEMA,'phase':'qualification','status':'passed','geometry_status':'passed','smoke_status':'passed',
            'physics_probes_status':'passed','regression_status':'passed','modes':list(MODES),'configurations':expected,'source_files_sha256':sources,
            'executable_sha256':exe_hash,'evidence':evidence,
            'scope':'Fixed 40-configuration/8000-beta matrix; legacy remains a distinct known-biased tally definition, not true entry.'}
    Path(output).parent.mkdir(parents=True,exist_ok=True);write_json(output,result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('geometry','smoke','probes','regression'):
        p.add_argument('--'+name,type=Path,required=True);p.add_argument('--'+name+'-campaign',type=Path,required=True)
    p.add_argument('--regression-reference',type=Path,required=True)
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--executable',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=qualify({n:getattr(a,n) for n in ('geometry','smoke','probes','regression')},
        {n:getattr(a,n+'_campaign') for n in ('geometry','smoke','probes','regression')},a.repo,a.executable,a.output,a.regression_reference)
    print('Qualified fixed 8000-beta comparison from four verified gate bundles: '+str(a.output))

if __name__=='__main__':main()
