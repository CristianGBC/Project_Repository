import hashlib, json, os, sqlite3, time, uuid
from contextlib import contextmanager
from threading import Lock
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
import httpx, pika

app = FastAPI(title='Distributed Academic Repository - Workshop 6')
NODES = os.getenv('NODES', 'node1,node2,node3,node4,node5,node6').split(',')
DB_PATH = os.getenv('DB_PATH', '/data/metadata.sqlite3')
LOCK = Lock()

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

@app.on_event('startup')
def init():
    with db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS versions (doc TEXT, version INTEGER, filename TEXT, checksum TEXT, nodes TEXT, created REAL, PRIMARY KEY(doc, version))')
        conn.execute('CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT, delivered INTEGER DEFAULT 0)')

def event(conn, kind, doc, version):
    conn.execute('INSERT INTO outbox(payload) VALUES (?)', (json.dumps({'event': kind, 'document_id': doc, 'version': version}),))

def flush_events():
    try:
        conn = pika.BlockingConnection(pika.ConnectionParameters(host=os.getenv('RABBIT_HOST', 'rabbitmq')))
        ch = conn.channel()
        ch.exchange_declare(exchange='repository.events', exchange_type='fanout', durable=True)
        with db() as sql:
            for ident, payload in sql.execute('SELECT id,payload FROM outbox WHERE delivered=0 ORDER BY id').fetchall():
                ch.basic_publish(exchange='repository.events', routing_key='', body=payload, properties=pika.BasicProperties(delivery_mode=2))
                sql.execute('UPDATE outbox SET delivered=1 WHERE id=?', (ident,))
        conn.close()
    except Exception as exc:
        print('Event pending for retry:', exc, flush=True)

@app.get('/health')
def health():
    return {'status':'ok', 'service':'repository-api'}

@app.get('/nodes')
async def nodes():
    async with httpx.AsyncClient(timeout=2) as client:
        result = []
        for node in NODES:
            try:
                r = await client.get(f'http://{node}:8000/health')
                result.append({'node':node, 'available':r.status_code==200})
            except httpx.HTTPError:
                result.append({'node':node, 'available':False})
        return result

@app.post('/documents')
async def create(file: UploadFile = File(...), school: str = Form('general')):
    return await save(None, file, school)

@app.put('/documents/{doc}')
async def update(doc: str, file: UploadFile = File(...), school: str = Form('general')):
    return await save(doc, file, school)

async def save(doc, file, school):
    # Serialize version assignment and writes in this single-process educational prototype.
    with LOCK:
        payload = await file.read()
        if not payload:
            raise HTTPException(400, 'Empty file')
        doc = doc or uuid.uuid4().hex
        if not doc.isalnum():
            raise HTTPException(400, 'Invalid document ID')
        checksum = hashlib.sha256(payload).hexdigest()
        with db() as conn:
            row = conn.execute('SELECT MAX(version) FROM versions WHERE doc=?', (doc,)).fetchone()
            if row[0] is None and file is None:
                raise HTTPException(404, 'Unknown document')
            if row[0] is None and doc is not None and not (len(doc)==32):
                raise HTTPException(404, 'Unknown document')
            version = (row[0] or 0) + 1
        successes = []
        async with httpx.AsyncClient(timeout=8) as client:
            for node in NODES:
                try:
                    ping = await client.get(f'http://{node}:8000/health', timeout=2)
                    if ping.status_code != 200:
                        continue
                    resp = await client.put(f'http://{node}:8000/files/{doc}/{version}', content=payload)
                    resp.raise_for_status()
                    if resp.json()['sha256'] == checksum:
                        successes.append(node)
                    if len(successes) == 3:
                        break
                except (httpx.HTTPError, KeyError, ValueError):
                    continue
            if len(successes) < 3:
                for node in successes:
                    try: await client.delete(f'http://{node}:8000/files/{doc}/{version}')
                    except httpx.HTTPError: pass
                raise HTTPException(503, 'Could not verify three replicas; previous version preserved')
        with db() as conn:
            conn.execute('INSERT INTO versions VALUES (?,?,?,?,?,?)', (doc,version,file.filename,checksum,json.dumps(successes),time.time()))
            event(conn, 'document.created' if version==1 else 'document.updated', doc, version)
            old = conn.execute('SELECT version,nodes FROM versions WHERE doc=? ORDER BY version DESC LIMIT -1 OFFSET 3', (doc,)).fetchall()
        flush_events()
        # Cleanup happens after publication; errors are logged and can be retried manually.
        async with httpx.AsyncClient(timeout=5) as client:
            for old_version, old_nodes in old:
                failures=[]
                for node in json.loads(old_nodes):
                    try:
                        response = await client.delete(f'http://{node}:8000/files/{doc}/{old_version}')
                        response.raise_for_status()
                    except httpx.HTTPError:
                        failures.append(node)
                if not failures:
                    with db() as conn:
                        conn.execute('DELETE FROM versions WHERE doc=? AND version=?', (doc,old_version))
        return {'document_id':doc, 'version':version, 'sha256':checksum, 'replicas':successes, 'school':school}

@app.get('/documents/{doc}/versions')
def versions(doc: str):
    with db() as conn:
        rows=conn.execute('SELECT version,filename,checksum,nodes FROM versions WHERE doc=? ORDER BY version DESC', (doc,)).fetchall()
    if not rows: raise HTTPException(404, 'Document not found')
    return [{'version':v,'filename':name,'sha256':sha,'replicas':json.loads(nodes)} for v,name,sha,nodes in rows]

@app.get('/documents/{doc}')
async def download(doc: str, version: int | None = None):
    with db() as conn:
        row = conn.execute('SELECT version,filename,checksum,nodes FROM versions WHERE doc=? AND version=COALESCE(?, (SELECT MAX(version) FROM versions WHERE doc=?))', (doc,version,doc)).fetchone()
    if not row: raise HTTPException(404, 'Document version not found')
    ver, filename, checksum, nodes = row
    async with httpx.AsyncClient(timeout=6) as client:
        for node in json.loads(nodes):
            try:
                r = await client.get(f'http://{node}:8000/files/{doc}/{ver}')
                if r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == checksum:
                    return Response(r.content, media_type='application/octet-stream', headers={'Content-Disposition':f'attachment; filename="{os.path.basename(filename)}"', 'X-Document-Version':str(ver)})
            except httpx.HTTPError:
                pass
    raise HTTPException(503, 'No valid replica available')

@app.post('/events/retry')
def retry_events():
    flush_events()
    with db() as conn:
        count=conn.execute('SELECT COUNT(*) FROM outbox WHERE delivered=0').fetchone()[0]
    return {'pending_events':count}
