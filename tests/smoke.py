"""Integration test against the running local stack; only removes its own test data."""
import base64
import io
import json
import os
import sys
import uuid
import zipfile
from pathlib import Path
import httpx
from PIL import Image
import fitz

root = Path(__file__).resolve().parents[1]
env = dict(line.split('=',1) for line in (root / '.env').read_text().splitlines() if '=' in line)
base = os.getenv('TEST_URL', 'http://localhost:' + env.get('LOCAL_PORT','6900'))
client = httpx.Client(base_url=base, headers={'X-Argilla-Api-Key': env['OWNER_API_KEY']}, timeout=120)
created = []
users = []
def req(method,path,**kwargs):
    r = client.request(method,path,**kwargs)
    assert r.is_success, (r.status_code,r.text[:600])
    return r.json() if r.content else {}
try:
    assert httpx.get(base + '/local/api/state').status_code == 401
    state = req('GET','/local/api/state')
    workspace = state['workspaces'][0]['id']
    img = Image.new('RGB',(640,800),'white'); b=io.BytesIO();img.save(b,'PNG')
    box={'x0':40,'y0':60,'x1':400,'y1':120,'label':'Text','text':'سلام دنیا'}
    row={'id':'same-source-id','image':'image.png','boxes':[box]}
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('records.jsonl',json.dumps(row,ensure_ascii=False)+'\n');z.writestr('image.png',b.getvalue())
    for n in range(2):
        result=req('POST','/local/api/import',data={'name':'smoke-'+str(uuid.uuid4()),'workspace':workspace},files={'file':('pages.zip',archive.getvalue(),'application/zip')})
        assert result['records']==1
        created.append(result['dataset_id'])
    records=[req('GET',f'/api/v1/datasets/{did}/records')['items'][0] for did in created]
    assert records[0]['external_id'] != records[1]['external_id']
    page=records[0]['external_id'];path='/seed-layout-edits/'+page
    assert httpx.get(base+path).status_code==401
    assert req('GET',path)['exists'] is False
    edited=[dict(box,content_text='متن اصلاح‌شده',text='متن اصلاح‌شده')]
    req('POST',path,json={'page_id':page,'boxes':edited})
    assert req('GET',path)['boxes']==edited
    annotator=req('POST','/api/v1/users',json={'first_name':'Smoke','username':'smoke-'+uuid.uuid4().hex,'password':uuid.uuid4().hex,'role':'annotator'})
    users.append(annotator['id'])
    other=httpx.Client(base_url=base,headers={'X-Argilla-Api-Key':annotator['api_key']},timeout=60)
    assert other.get(path).status_code==403
    req('POST',f'/api/v1/workspaces/{workspace}/users',json={'user_id':annotator['id']})
    assert other.get(path).json()['exists'] is False
    assert other.post(path,json={'page_id':page,'boxes':[]}).is_success
    assert req('GET',path)['boxes']==edited
    assert other.get(path).json()['boxes']==[]
    other.close()

    assert req('GET','/seed-layout-edits/'+records[1]['external_id'])['exists'] is False
    review=req('POST',f'/api/v1/records/{records[0]["id"]}/responses',json={'status':'submitted','values':{'review_status':{'value':'accept'}}})
    r=client.get('/local/api/export/'+created[0]);assert r.is_success,(r.status_code,r.text[:400])
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        exported=json.loads(z.read('records.jsonl').splitlines()[0])
        assert any(a['boxes']==edited for a in exported['local_annotations'])
        assert len(exported['local_annotations'])==2
        assert exported['responses'][0]['values']['review_status']['value']=='accept'
        assert exported['fields']['label']['src'].startswith('data:image/jpeg;base64,')
        assert exported['fields']['label']['items'][0]['text']=='سلام دنیا'
    doc=fitz.open();doc.new_page();doc.new_page();pdf=doc.tobytes();doc.close()
    result=req('POST','/local/api/import',data={'name':'smoke-pdf-'+str(uuid.uuid4()),'workspace':workspace},files={'file':('test.pdf',pdf,'application/pdf')})
    created.append(result['dataset_id']);assert result['records']==2
    # Names cannot overwrite an existing dataset.
    name=req('GET','/api/v1/datasets/'+created[0])['name']
    assert client.post('/local/api/import',data={'name':name,'workspace':workspace},files={'file':('image.png',b.getvalue(),'image/png')}).status_code==409
    print('PASS: auth + workspace access, ZIP + PDF import, dataset + annotator isolation, Persian edits, submitted review, provenance-preserving export, duplicate protection')
finally:
    for did in created:
        req('DELETE','/api/v1/datasets/'+did)
    for uid in users:
        req('DELETE','/api/v1/users/'+uid)
