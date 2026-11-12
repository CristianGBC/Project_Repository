import base64
import binascii
import hashlib
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from jsonschema import Draft202012Validator, ValidationError


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
NODE_NAME = os.getenv("NODE_NAME", "school-of-computing")

with (BASE_DIR / "schemas/store_request.schema.json").open(encoding="utf-8") as file:
    REQUEST_SCHEMA = json.load(file)
with (BASE_DIR / "schemas/store_response.schema.json").open(encoding="utf-8") as file:
    RESPONSE_SCHEMA = json.load(file)

REQUEST_VALIDATOR = Draft202012Validator(REQUEST_SCHEMA)
RESPONSE_VALIDATOR = Draft202012Validator(RESPONSE_SCHEMA)

DATA_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Workshop 7 - JSON storage node")


def path_for(document_id: str, version: int) -> Path:
    if not document_id.isalnum() or version < 1:
        raise HTTPException(status_code=400, detail="Invalid document identifier")
    return DATA_DIR / f"{document_id}_v{version}.bin"


@app.get("/health")
def health():
    return {"status": "ok", "node": NODE_NAME}


@app.post("/schema/files")
async def store_with_schema(request: Request):
    processing_started = time.perf_counter()
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Malformed JSON") from None

    validation_started = time.perf_counter()
    try:
        REQUEST_VALIDATOR.validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    validation_time_ms = (time.perf_counter() - validation_started) * 1000

    try:
        payload = base64.b64decode(data["content_base64"], validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid Base64 content") from None

    checksum = hashlib.sha256(payload).hexdigest()
    if checksum != data["sha256"]:
        raise HTTPException(status_code=400, detail="Checksum mismatch")

    destination = path_for(data["document_id"], data["version"])
    if destination.exists() and destination.read_bytes() != payload:
        raise HTTPException(status_code=409, detail="Immutable version already exists")
    destination.write_bytes(payload)

    processing_time_ms = (time.perf_counter() - processing_started) * 1000
    response = {
        "stored": True,
        "document_id": data["document_id"],
        "version": data["version"],
        "sha256": checksum,
        "node": NODE_NAME,
        "size_bytes": len(payload),
        "processing_time_ms": processing_time_ms,
        "validation_time_ms": validation_time_ms,
    }
    try:
        RESPONSE_VALIDATOR.validate(response)
    except ValidationError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid server response: {exc.message}") from None
    return response


@app.get("/files/{document_id}/{version}/info")
def file_info_without_schema(document_id: str, version: int):
    processing_started = time.perf_counter()
    destination = path_for(document_id, version)
    exists = destination.exists()
    payload = destination.read_bytes() if exists else b""
    processing_time_ms = (time.perf_counter() - processing_started) * 1000
    return {
        "document_id": document_id,
        "version": version,
        "exists": exists,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest() if exists else None,
        "node": NODE_NAME,
        "processing_time_ms": processing_time_ms,
    }


@app.post("/no-schema/files/info")
async def file_info_from_json_without_schema(request: Request):
    processing_started = time.perf_counter()
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Malformed JSON") from None

    # These are deliberate manual checks. Activity 2 does not use JSON Schema.
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="JSON object expected")
    if "document_id" not in data or "version" not in data:
        raise HTTPException(
            status_code=400,
            detail="document_id and version are required",
        )

    document_id = data["document_id"]
    version = data["version"]
    if not isinstance(document_id, str) or not document_id.isalnum():
        raise HTTPException(status_code=400, detail="Invalid document_id")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise HTTPException(status_code=400, detail="Invalid version")

    destination = path_for(document_id, version)
    exists = destination.exists()
    payload = destination.read_bytes() if exists else b""
    processing_time_ms = (time.perf_counter() - processing_started) * 1000
    return {
        "document_id": document_id,
        "version": version,
        "exists": exists,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest() if exists else None,
        "node": NODE_NAME,
        "processing_time_ms": processing_time_ms,
    }
