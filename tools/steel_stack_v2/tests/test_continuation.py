import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import continue_corner_acceptance as continuation
import run as infra
from deliver import inventories

class ContinuationTests(unittest.TestCase):
    def make_build(self,root):
        source=root/'original';(source/'source').mkdir(parents=True);(source/'build').mkdir()
        infra.save(source/'source-manifest.json',{'source_files':{}})
        infra.save(source/'environment.json',{})
        binary=source/'build/OpNovice2';binary.write_bytes(b'fixture-binary-identity')
        cache=source/'build/CMakeCache.txt';cache.write_text('CMAKE_HOME_DIRECTORY:INTERNAL=/fixture\n')
        original=dict(path=str(binary),sha256=infra.sha(binary),cmake_cache_sha256=infra.sha(cache))
        infra.save(source/'binary.json',original)
        receipt=root/'run-receipt.json'
        infra.save(receipt,dict(accepted=True,source_manifest_sha256=infra.sha(source/'source-manifest.json'),executable_sha256=infra.sha(binary)))
        return source,receipt

    def test_missing_binding_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,_=self.make_build(root)
            with self.assertRaisesRegex(ValueError,'no verified run receipts'):
                continuation.import_build(source,root/'imported')

    def test_wrong_run_source_binding_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(infra.local,'cpath',return_value='/fixture'):
            root=Path(tmp);source,receipt=self.make_build(root)
            value=infra.read(receipt);value['source_manifest_sha256']='wrong';infra.save(receipt,value)
            with self.assertRaisesRegex(ValueError,'does not bind'):
                continuation.import_build(source,root/'imported',[receipt])

    def test_import_keeps_original_receipt_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(infra.local,'cpath',return_value='/fixture'):
            root=Path(tmp);source,receipt=self.make_build(root)
            original=(source/'binary.json').read_bytes()
            continuation.import_build(source,root/'imported',[receipt])
            imported=infra.read(root/'imported/binary.json')
            self.assertEqual((source/'binary.json').read_bytes(),original)
            self.assertEqual(imported['source_manifest_sha256'],infra.sha(source/'source-manifest.json'))
            self.assertEqual(imported['source_binding_receipts'][0]['sha256'],infra.sha(receipt))
            self.assertEqual(imported['sha256'],infra.sha(imported['path']))

    def test_pending_inventories_keep_promoted_numerics_and_no_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);profile={'profile':'painted-corner-v2','scale':16}
            infra.save(root/'manifest.json',{'optical_numerics':profile})
            inventories(root)
            for name,count in [('sensitivity',8),('full',48)]:
                doc=infra.read(root/'pending-scans'/f'{name}-configurations.json')
                self.assertEqual(len(doc['configurations']),count)
                self.assertEqual(doc['scientific_events'],0)
                self.assertIsNone(doc['total_event_budget'])
                self.assertTrue(all(c['optical_numerics']==profile for c in doc['configurations']))

if __name__=='__main__':unittest.main()
