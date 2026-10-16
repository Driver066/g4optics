#!/usr/bin/env python3
"""Audit the independent 40-cell layout/readout study without simulation."""
import argparse
import json
from pathlib import Path
from study import write_json
from validation import audit

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign',required=True,type=Path)
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--executable',type=Path)
    p.add_argument('--report',type=Path)
    a=p.parse_args();result=audit(a.campaign,a.repo,a.executable)
    if a.report:write_json(a.report,result)
    print(json.dumps(result,indent=2,allow_nan=False))

if __name__=='__main__':main()
