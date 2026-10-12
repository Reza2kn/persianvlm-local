"""Local import/export companion and same-origin Argilla proxy."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from PIL import Image, ImageOps
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

ARGILLA = os.getenv('ARGILLA_URL', 'http://argilla:6900').rstrip('/')
DATA = Path(os.getenv('DATA_DIR', '/data'))
DATA.mkdir(parents=True, exist_ok=True)
ROOT = Path(__file__).parent
app = FastAPI(title='PersianVLM Local')
http = httpx.AsyncClient(timeout=300, follow_redirects=False)
MAX_UPLOAD = 512 * 1024 * 1024


def db():
    conn = sqlite3.connect(DATA / 'edits.sqlite', timeout=30)
    conn.execute('CREATE TABLE IF NOT EXISTS pages (id TEXT PRIMARY KEY, dataset TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS edits (page TEXT, user TEXT, boxes TEXT, updated TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(page,user))')
    return conn


def auth_headers(request: Request):
    headers = {}
    for name in ('authorization', 'x-argilla-api-key'):
        if request.headers.get(name):
            headers[name] = request.headers[name]
    if not headers:
        raise HTTPException(401, 'Sign in to Argilla first, then open /local.')
    return headers


async def api(method: str, path: str, headers: dict, payload=None):
    r = await http.request(method, ARGILLA + '/api/v1' + path, headers=headers, json=payload)
    if r.is_error:
        raise HTTPException(r.status_code, r.text[:1000])
    return r.json() if r.content else {}


async def collection(path: str, headers: dict):
    offset = 0
    while True:
        sep = '&' if '?' in path else '?'
        result = await api('GET', f'{path}{sep}limit=100&offset={offset}', headers)
        rows = result.get('items', [])
        for row in rows:
            yield row
        offset += len(rows)
        if not rows or ('total' not in result and len(rows) < 100) or offset >= result.get('total', offset + 1):
            break


def image_uri(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get('bytes') or value.get('path')
    if isinstance(value, str) and value.startswith('data:image/'):
        value = base64.b64decode(value.split(',', 1)[1], validate=True)
    if isinstance(value, (str, Path)):
        value = Path(value).read_bytes()
    if isinstance(value, bytes):
        value = Image.open(io.BytesIO(value))
    if not isinstance(value, Image.Image):
        raise ValueError('Expected an image, image path or image data URI.')
    value = ImageOps.exif_transpose(value).convert('RGB')
    out = io.BytesIO()
    value.save(out, format='JPEG', quality=94)
    return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode()


def row_record(row: dict, dataset: str, index: int, base: Path | None = None):
    label = row.get('fields', {}).get('label', row.get('label', {}))
    if not isinstance(label, dict):
        label = {'items': label if isinstance(label, list) else []}
    source = row.get('image') or label.get('src') or row.get('fields', {}).get('image')
    if isinstance(source, str) and not source.startswith('data:') and base:
        candidate = (base / source).resolve()
        if not candidate.is_relative_to(base.resolve()):
            raise ValueError('Image paths must stay inside the import directory.')
        source = candidate
    src = image_uri(source)
    original_id = str(row.get('external_id') or row.get('id') or f'page-{index:06d}')
    page = dataset + ':' + hashlib.sha256(original_id.encode()).hexdigest()[:24]
    boxes = label.get('items', row.get('boxes', []))
    if not boxes and label.get('layout_boxes_json'):
        boxes = json.loads(label['layout_boxes_json'])
    if not isinstance(boxes, list) or any(not isinstance(box, dict) for box in boxes):
        raise ValueError('Boxes must be a list of objects.')
    return {'external_id': page, 'fields': {'label': {'src': src, 'items': boxes}},
            'metadata': {'source_id': original_id, 'source_filename': str(row.get('filename', original_id)),
                         'imported_as': 'unreviewed', 'source_metadata_json': json.dumps(row.get('metadata', {}), ensure_ascii=False)}}


def uploaded_rows(path: Path, filename: str):
    suffix = Path(filename).suffix.lower()
    if suffix == '.pdf':
        import fitz
        with fitz.open(path) as doc:
            for i, page in enumerate(doc):
                yield {'id': f'{filename}:page-{i+1}', 'filename': f'{filename}:page-{i+1}',
                       'image': page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes('png')}
    elif suffix == '.zip':
        with zipfile.ZipFile(path) as z:
            entries = [e for e in z.infolist() if not e.is_dir()]
            if sum(e.file_size for e in entries) > 2 * 1024**3:
                raise ValueError('ZIP expands beyond the 2 GiB limit.')
            manifests = [e for e in entries if e.filename.lower().endswith('.jsonl')]
            if manifests:
                if len(manifests) != 1:
                    raise ValueError('Use one JSONL manifest per ZIP.')
                manifest = manifests[0]
                base = Path(manifest.filename).parent
                for line in z.read(manifest).decode('utf-8-sig').splitlines():
                    if not line.strip(): continue
                    row = json.loads(line)
                    label = row.get('fields', {}).get('label', row.get('label', {}))
                    src = row.get('image') or (label.get('src') if isinstance(label, dict) else None)
                    if isinstance(src, str) and not src.startswith('data:'):
                        image = base / src
                        if image.is_absolute() or '..' in image.parts:
                            raise ValueError('ZIP image paths must be relative, with no parent traversal.')
                        row['image'] = z.read(image.as_posix())
                    yield row
            else:
                for e in sorted(entries, key=lambda e: e.filename):
                    if Path(e.filename).suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp'):
                        yield {'id': e.filename, 'filename': e.filename, 'image': z.read(e)}
    elif suffix == '.jsonl':
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip():
                row = json.loads(line)
                # Single-file browser imports cannot resolve arbitrary host paths.
                src = row.get('image') or row.get('fields', {}).get('label', {}).get('src')
                if not isinstance(src, str) or not src.startswith('data:image/'):
                    raise ValueError('JSONL upload requires data:image URIs; use ZIP for relative image files.')
                yield row
    else:
        yield {'id': filename, 'filename': filename, 'image': path.read_bytes()}


async def create_dataset(name, workspace, headers):
    if not name.strip(): raise HTTPException(400, 'Enter a dataset name.')
    existing = [d async for d in collection('/me/datasets', headers)]
    if any(d['name'] == name and d['workspace_id'] == workspace for d in existing):
        raise HTTPException(409, 'That dataset already exists. Use a new name; existing annotations are never overwritten.')
    dataset = await api('POST', '/datasets', headers, {'name': name, 'workspace_id': workspace,
        'guidelines': 'کادرها و متن را با تصویر تطبیق دهید. داده‌های واردشده پیش‌نویس هستند. پس از بررسی، نتیجه را ثبت کنید.',
        'allow_extra_metadata': True})
    did = dataset['id']
    await api('POST', f'/datasets/{did}/fields', headers, {'name': 'label', 'title': 'تصویر و کادرها', 'required': True,
        'settings': {'type': 'custom', 'template': (ROOT / 'templates/editor.html').read_text(), 'advanced_mode': True}})
    for q in [
        {'name': 'review_status', 'title': 'وضعیت بازبینی', 'required': True, 'settings': {'type': 'label_selection', 'options': [
            {'value': 'accept', 'text': 'تأیید'}, {'value': 'needs_fix', 'text': 'نیازمند اصلاح'}, {'value': 'reject', 'text': 'رد'}]}}]:
        await api('POST', f'/datasets/{did}/questions', headers, q)
    await api('PUT', f'/datasets/{did}/publish', headers)
    return did


async def import_rows(rows, did, headers):
    count = 0
    seen = set()
    iterator = iter(rows)
    # Parsing/rasterizing/download work is moved off the async server thread.
    def next_row():
        try: return next(iterator)
        except StopIteration: return None
    try:
        while True:
            row = await run_in_threadpool(next_row)
            if row is None: break
            record = await run_in_threadpool(row_record, row, did, count + 1)
            if record['external_id'] in seen: raise ValueError('Duplicate source ID in import; use unique page IDs.')
            seen.add(record['external_id'])
            await api('POST', f'/datasets/{did}/records/bulk', headers, {'items': [record]})
            with db() as conn:
                conn.execute('INSERT OR IGNORE INTO pages VALUES (?,?)', (record['external_id'], did))
            count += 1
        if count == 0: raise ValueError('No images found.')
    except Exception as exc:
        # Retain partially imported dataset, without pretending it completed.
        raise HTTPException(400, f'Import stopped after {count} records. Dataset {did} was retained. {str(exc)[:500]}') from exc
    return {'dataset_id': did, 'records': count, 'url': f'/dataset/{did}/annotation-mode'}


@app.get('/local', response_class=HTMLResponse)
async def portal():
    return (ROOT / 'static/index.html').read_text()


@app.get('/local/font.ttf')
async def font():
    return FileResponse(ROOT / 'static/Vazirmatn.ttf', media_type='font/ttf')


@app.get('/local/health')
async def health():
    r = await http.get(ARGILLA + '/api/status')
    return JSONResponse({'argilla': r.status_code == 200}, status_code=200 if r.status_code == 200 else 503)


@app.get('/local/api/state')
async def state(request: Request):
    headers = auth_headers(request)
    return {'user': await api('GET', '/me', headers), 'workspaces': [w async for w in collection('/me/workspaces', headers)],
            'datasets': [d async for d in collection('/me/datasets', headers)]}


@app.post('/local/api/import')
async def import_file(request: Request, name: str = Form(...), workspace: str = Form(...), file: UploadFile = File(...)):
    headers = auth_headers(request)
    await api('GET', '/me', headers)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'upload'
        size = 0
        with path.open('wb') as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD: raise HTTPException(413, 'Maximum upload is 512 MiB. Split larger imports.')
                out.write(chunk)
        # Preflight before creating a dataset catches malformed inputs early.
        rows = uploaded_rows(path, file.filename or 'upload')
        try: first = await run_in_threadpool(next, rows)
        except Exception as exc:
            raise HTTPException(400, f'Could not read input: {exc}') from exc
        try:
            await run_in_threadpool(row_record, first, str(uuid.uuid4()), 1)
        except Exception as exc:
            raise HTTPException(400, f'Invalid first record: {str(exc)[:400]}') from exc
        did = await create_dataset(name, workspace, headers)
        import itertools
        return await import_rows(itertools.chain([first], rows), did, headers)


@app.post('/local/api/import-hf')
async def import_hf(request: Request):
    headers = auth_headers(request)
    await api('GET', '/me', headers)
    payload = await request.json()
    def load():
        from datasets import load_dataset
        dataset = load_dataset(payload['repo'], name=payload.get('config') or None,
            split=payload.get('split', 'train'), streaming=True, token=payload.get('token') or False)
        import itertools
        limit = int(payload.get('limit', 1000))
        if not 1 <= limit <= 100000: raise ValueError('Limit must be between 1 and 100000.')
        for i, row in enumerate(itertools.islice(dataset, limit)):
            yield {'id': str(row.get(payload.get('id_column', 'id'), i)),
                'image': row[payload.get('image_column', 'image')],
                'boxes': row.get(payload.get('boxes_column', 'boxes'), []) or []}
    rows = load()
    try:
        first = await run_in_threadpool(next, rows)
        await run_in_threadpool(row_record, first, str(uuid.uuid4()), 1)
    except Exception as exc: raise HTTPException(400, f'Hugging Face input failed: {str(exc)[:400]}') from exc
    did = await create_dataset(payload['name'], payload['workspace'], headers)
    import itertools
    return await import_rows(itertools.chain([first], rows), did, headers)


async def page_access(request, page):
    headers = auth_headers(request)
    user = await api('GET', '/me', headers)
    with db() as conn:
        found = conn.execute('SELECT dataset FROM pages WHERE id=?', (page,)).fetchone()
    if not found: raise HTTPException(404, 'Unknown page.')
    await api('GET', f'/datasets/{found[0]}', headers)
    return str(user['id'])


@app.get('/seed-layout-edits/{page:path}')
async def get_edits(page: str, request: Request):
    user = await page_access(request, page)
    with db() as conn:
        row = conn.execute('SELECT boxes,updated FROM edits WHERE page=? AND user=?', (page, user)).fetchone()
    return {'exists': bool(row), 'boxes': json.loads(row[0]) if row else [], 'updated': row[1] if row else None}


@app.post('/seed-layout-edits/{page:path}')
async def save_edits(page: str, request: Request):
    user = await page_access(request, page)
    data = await request.json()
    if data.get('page_id') != page or not isinstance(data.get('boxes'), list):
        raise HTTPException(400, 'Invalid edit payload.')
    if len(data['boxes']) > 10000 or any(not isinstance(b, dict) for b in data['boxes']):
        raise HTTPException(400, 'Invalid boxes.')
    with db() as conn:
        conn.execute('INSERT INTO edits(page,user,boxes) VALUES (?,?,?) ON CONFLICT(page,user) DO UPDATE SET boxes=excluded.boxes,updated=CURRENT_TIMESTAMP',
                     (page, user, json.dumps(data['boxes'], ensure_ascii=False)))
    return {'saved': True}


@app.get('/local/api/export/{did}')
async def export(did: str, request: Request):
    headers = auth_headers(request)
    dataset = await api('GET', f'/datasets/{did}', headers)
    user = await api('GET', '/me', headers)
    # Spool to disk, so embedded page images do not accumulate in RAM.
    temp = tempfile.TemporaryFile()
    with zipfile.ZipFile(temp, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('dataset.json', json.dumps(dataset, ensure_ascii=False, indent=2))
        for kind in ('fields', 'questions'):
            bundle.writestr(kind + '.json', json.dumps(await api('GET', f'/datasets/{did}/{kind}', headers), ensure_ascii=False))
        with bundle.open('records.jsonl', 'w') as out:
            async for record in collection(f'/datasets/{did}/records', headers):
                page = record['external_id']
                with db() as conn:
                    edits = conn.execute('SELECT user,boxes,updated FROM edits WHERE page=?' + ('' if user['role'] in ('owner', 'admin') else ' AND user=?'), (page,) if user['role'] in ('owner', 'admin') else (page, str(user['id']))).fetchall()
                record['local_annotations'] = [{'user_id': u, 'boxes': json.loads(b), 'updated': t} for u,b,t in edits]
                # Explicit response lookup preserves review status and contributor provenance.
                record['responses'] = (await api('GET', f'/records/{record["id"]}', headers)).get('responses', [])
                if user['role'] not in ('owner', 'admin'):
                    record['responses'] = [r for r in record['responses'] if str(r['user_id']) == str(user['id'])]
                out.write((json.dumps(record, ensure_ascii=False) + '\n').encode())
    temp.seek(0)
    def chunks():
        while chunk := temp.read(1024 * 1024): yield chunk
    return StreamingResponse(chunks(), media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="persianvlm-{did}.zip"'}, background=BackgroundTask(temp.close))


@app.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
async def proxy(path: str, request: Request):
    url = ARGILLA + '/' + path
    if request.url.query: url += '?' + request.url.query
    headers = {k:v for k,v in request.headers.items() if k.lower() not in ('host', 'connection', 'content-length', 'accept-encoding')}
    headers['accept-encoding'] = 'identity'
    req = http.build_request(request.method, url, headers=headers, content=await request.body())
    upstream = await http.send(req, stream=True)
    excluded = {'content-encoding', 'content-length', 'transfer-encoding', 'connection'}
    result = StreamingResponse(upstream.aiter_bytes(), status_code=upstream.status_code,
        background=BackgroundTask(upstream.aclose))
    result.raw_headers = [(k,v) for k,v in upstream.headers.raw if k.decode().lower() not in excluded]
    return result
