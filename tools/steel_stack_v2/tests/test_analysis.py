from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import analyze
import audit
from test_model_audit import ledger_fixture


def outside_and_transfer_fixture():
    scan,event,layer,sensor,flow,config=ledger_fixture(events=4,zero=True)
    # Variable events contain outside-born detections and photons born in tile 0
    # but collected in layer 1. These distinguish every denominator and ancestry.
    tile=[8,12,20,10];outside=[2,8,5,10];legacy_tile=[4,6,10,5]
    same=[2,2,3,1];cross=[1,2,3,1];outside_d=[1,6,2,5]
    rows=[]
    for e in range(4):
        total=tile[e]+outside[e];g=legacy_tile[e]+outside[e];d=same[e]+cross[e]+outside_d[e]
        values={"generated_legacy":g,"detected_legacy":d,"births_total":total,"births_nonoptical_parent":g,
                "births_optical_parent":tile[e]-legacy_tile[e],"births_scintillation":total,
                "births_tile":tile[e],"births_world":outside[e],"tracks_started":total,"tracks_finalized":total,
                "fate_sipm":d,"fate_bulk_tile":tile[e]-same[e]-cross[e],"fate_bulk_world":outside[e]-outside_d[e]}
        for key,value in values.items():event[key][e]=value
        ix=e*10
        for key,value in {"births_total":tile[e],"births_nonoptical_parent":legacy_tile[e],
                          "births_optical_parent":tile[e]-legacy_tile[e],"births_scintillation":tile[e],
                          "generated_legacy":legacy_tile[e],"scintillation_legacy":legacy_tile[e],
                          "detected_all_origins":same[e]+outside_d[e],"detected_same_root_layer":same[e],
                          "detected_same_birth_layer":same[e]}.items():layer[key][ix]=value
        layer["detected_all_origins"][ix+1]=cross[e]
        for key in ("detected_all_origins","detected_same_root_layer","detected_same_birth_layer"):
            sensor[key][ix]=layer[key][ix];sensor[key][ix+1]=layer[key][ix+1]
        sensor["detected_root_outside"][ix]=outside_d[e];sensor["detected_birth_outside"][ix]=outside_d[e]
        for count,parent,fate,destination in ((same[e],0,0,0),(cross[e],0,0,4),
                (legacy_tile[e]-same[e]-cross[e],0,1,-1),(tile[e]-legacy_tile[e],1,1,-1)):
            if count:rows.append([0,e,1,0,-1,1,0,-1,"Scintillation",parent,fate,destination,count])
        for count,fate,destination in ((outside_d[e],0,0),(outside[e]-outside_d[e],3,-1)):
            if count:rows.append([0,e,3,-1,-1,3,-1,-1,"Scintillation",0,fate,destination,count])
        scan["generated_optical_photons"][e]=g;scan["scintillation_photons"][e]=g;scan["sipm_detected_photons"][e]=d
    flow={key:np.array([row[i] for row in rows],dtype="U32" if key=="creator_process" else np.int64)
          for i,key in enumerate(flow)}
    audit.check_ledgers(scan,event,layer,sensor,flow,config,4)
    return event,layer,sensor,flow,config


class AnalysisTests(unittest.TestCase):
    def test_zero_denominators_remain_nan_with_flags(self):
        ratios,valid=analyze.safe_ratio([0,5,3],[0,0,6])
        self.assertTrue(np.isnan(ratios[:2]).all())
        np.testing.assert_array_equal(valid,[False,False,True])
        self.assertEqual(ratios[2],.5)
        result=analyze.estimate(float("nan"),np.full(10000,np.nan))
        self.assertFalse(result["valid"]);self.assertEqual(result["valid_resamples"],0)
        self.assertTrue(np.isnan(result["ci95_low"]))

    def test_non_degenerate_whole_event_bootstrap_and_covariance(self):
        tasks=[dict(task_id="block-a",events=20),dict(task_id="block-b",events=30)]
        weights=analyze.event_weights(tasks)
        self.assertEqual(weights.shape,(10000,50))
        np.testing.assert_array_equal(weights[:,:20].sum(1),np.full(10000,20))
        np.testing.assert_array_equal(weights[:,20:].sum(1),np.full(10000,30))
        values=np.arange(1.,51.)
        per_event=(weights@values)/50
        interval=analyze.estimate(values.mean(),per_event)
        self.assertLess(interval["ci95_low"],interval["ci95_high"])
        # A perfectly correlated D=.2*births relationship must be preserved.
        ratios,_=analyze.safe_ratio(weights@(values*.2),weights@values)
        np.testing.assert_allclose(ratios,.2,rtol=1e-14)
        np.testing.assert_array_equal(weights,analyze.event_weights(tasks))

    def test_engineering_smokes_are_explicitly_allowed_but_not_pooled_with_repeats(self):
        common=dict(config={"study_preset":"steel-module-stack-v2"},accounting_enabled=True,
                    independent_sample=False,purpose="engineering-acceptance-not-scientific-evidence",seed1=1,seed2=2)
        tasks=[dict(common,task_id="smoke",stage="smoke"),dict(common,task_id="repeat",stage="repeat"),
               dict(common,task_id="on",stage="noninterference"),dict(common,task_id="off",stage="smoke",accounting_enabled=False)]
        self.assertEqual([t["task_id"] for t in analyze.select_tasks(tasks,engineering=True)],["smoke"])
        with self.assertRaises(ValueError):analyze.select_tasks(tasks,engineering=False)

    def test_ratio_comparison_with_zero_reference_is_invalid_not_zero(self):
        metrics=("net","legacy_ce","birth_ce","net_per_sensor","net_per_active_mm2")
        left=dict(configuration_hash="a",layout="back-center",gap_mm=.5,tile_thickness_mm=4,**{m:0. for m in metrics})
        right=dict(configuration_hash="b",layout="back-four",gap_mm=.5,tile_thickness_mm=4,**{m:1. for m in metrics})
        boot={"a":{m:np.zeros(10000) for m in metrics},"b":{m:np.ones(10000) for m in metrics}}
        rows=analyze.comparisons([left,right],boot)
        self.assertEqual(len(rows),5)
        self.assertTrue(all(row["difference"]==1 and row["difference_valid"] for row in rows))
        self.assertTrue(all(not row["ratio_valid"] and np.isnan(row["ratio"]) for row in rows))

    def test_tile_birth_denominator_and_historical_same_root_metric_are_distinct(self):
        event,layer,sensor,flow,config=outside_and_transfer_fixture()
        tasks=[dict(task_id="origin-block0",events=2,config=config),dict(task_id="origin-block1",events=2,config=config)]
        with patch.object(analyze,"read_group",return_value=(event,layer,sensor,flow)):
            summary,boot,layers,sensors,*_=analyze.summarize_group(tasks,[None,None])
        self.assertAlmostEqual(summary["birth_ce"],15/50)  # Tile-born collection includes transfers to layer 1.
        self.assertAlmostEqual(summary["all_birth_ce"],29/75)
        self.assertAlmostEqual(summary["legacy_ce"],29/50)
        self.assertAlmostEqual(summary["same_root_legacy_ce"],8/25)
        self.assertAlmostEqual(layers[0]["legacy_ce"],8/25)
        self.assertAlmostEqual(layers[0]["all_origin_legacy_ce"],22/25)
        self.assertAlmostEqual(sensors[0]["legacy_ce"],8/25)
        self.assertAlmostEqual(sensors[0]["all_origin_legacy_ce"],22/25)
        self.assertLess(summary["birth_ce_ci95_low"],summary["birth_ce_ci95_high"])
        self.assertFalse(np.array_equal(boot["birth_ce"],boot["all_birth_ce"]))


if __name__=="__main__":unittest.main()
