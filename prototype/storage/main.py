from pathlib import Path
import hashlib
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

app = FastAPI(title="Storage node")
ROOT = Path('/data')
ROOT.mkdir(parents=True, exist_ok=True)

def path_for(doc: str, version: int):
    if not doc.isalnum() or version < 1:
        raise HTTPException(400, 'Invalid identifier')
    return ROOT / f'{doc}_v{version}.bin'

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.put('/files/{doc}/{version}')
async def store(doc: str, version: int, request: Request):
    payload = await request.body()
    p = path_for(doc, version)
    if p.exists() and p.read_bytes() != payload:
        raise HTTPException(409, 'Immutable version already exists')
    p.write_bytes(payload)
    return {'sha256': hashlib.sha256(payload).hexdigest()}

@app.get('/files/{doc}/{version}')
def get_file(doc: str, version: int):
    p = path_for(doc, version)
    if not p.exists():
        raise HTTPException(404, 'File not found')
    return Response(p.read_bytes(), media_type='application/octet-stream')

@app.delete('/files/{doc}/{version}')
def delete_file(doc: str, version: int):
    path_for(doc, version).unlink(missing_ok=True)
    return {'deleted': True}
