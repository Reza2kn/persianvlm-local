import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from PIL import Image

spec=importlib.util.spec_from_file_location('main',Path(__file__).resolve().parents[1]/'app/main.py')
main=importlib.util.module_from_spec(spec);spec.loader.exec_module(main)

class ImportTests(unittest.TestCase):
    def setUp(self):
        out=io.BytesIO();Image.new('RGB',(100,120),'white').save(out,'PNG');self.image=out.getvalue()
    def test_datasets_namespace_page_ids(self):
        row={'id':'1','image':self.image,'boxes':[{'text':'سلام'}]}
        a=main.row_record(row,'dataset-a',1);b=main.row_record(row,'dataset-b',1)
        self.assertNotEqual(a['external_id'],b['external_id'])
        self.assertEqual(a['metadata']['imported_as'],'unreviewed')
        self.assertEqual(a['fields']['label']['items'][0]['text'],'سلام')
    def test_zip_parent_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'file.zip'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('records.jsonl',json.dumps({'image':'../outside.png'}));z.writestr('../outside.png',self.image)
            with self.assertRaises(ValueError):list(main.uploaded_rows(p,'file.zip'))
    def test_zip_manifest_image(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'file.zip'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('records.jsonl',json.dumps({'image':'scans/1.png','id':'original'}));z.writestr('scans/1.png',self.image)
            row=list(main.uploaded_rows(p,'file.zip'))[0]
            record=main.row_record(row,'dataset',1)
            self.assertEqual(record['metadata']['source_id'],'original')
            self.assertTrue(record['fields']['label']['src'].startswith('data:image/jpeg;'))
if __name__=='__main__':unittest.main()
