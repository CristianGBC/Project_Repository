import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from jsonschema import Draft202012Validator, ValidationError


BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_URL = os.getenv("STORAGE_URL", "http://storage:8000").rstrip("/")

with (BASE_DIR / "schemas/store_request.schema.json").open(encoding="utf-8") as file:
    REQUEST_VALIDATOR = Draft202012Validator(json.load(file))
with (BASE_DIR / "schemas/store_response.schema.json").open(encoding="utf-8") as file:
    RESPONSE_VALIDATOR = Draft202012Validator(json.load(file))

app = FastAPI(title="Workshop 7 - JSON coordinator")


def compact_json(data: dict) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@app.get("/health")
def health():
    return {"status": "ok", "storage_url": STORAGE_URL}


@app.post("/documents/schema")
async def upload_with_schema(
    file: UploadFile = File(...),
    document_id: str | None = None,
    version: int = 1,
):
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty file")

    request_data = {
        "document_id": document_id or uuid.uuid4().hex,
        "version": version,
        "filename": Path(file.filename or "document.bin").name,
        "content_base64": base64.b64encode(payload).decode("ascii"),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    validation_started = time.perf_counter()
    try:
        REQUEST_VALIDATOR.validate(request_data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    client_validation_ms = (time.perf_counter() - validation_started) * 1000

    encoded_request = compact_json(request_data)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            remote = await client.post(
                f"{STORAGE_URL}/schema/files",
                content=encoded_request,
                headers={"Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Storage node unavailable: {exc}") from None
    latency_ms = (time.perf_counter() - started) * 1000

    if remote.is_error:
        raise HTTPException(status_code=remote.status_code, detail=remote.text)
    try:
        response_data = remote.json()
        RESPONSE_VALIDATOR.validate(response_data)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=502, detail=f"Invalid storage response: {exc}") from None

    return {
        "service_response": response_data,
        "metrics": {
            "original_file_bytes": len(payload),
            "request_body_bytes": len(encoded_request),
            "response_body_bytes": len(remote.content),
            "latency_ms": latency_ms,
            "client_validation_time_ms": client_validation_ms,
        },
    }


@app.get("/documents/{document_id}/{version}/info")
async def get_info_without_schema(document_id: str, version: int):
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            remote = await client.get(
                f"{STORAGE_URL}/files/{document_id}/{version}/info"
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"Storage node unavailable: {exc}") from None
    latency_ms = (time.perf_counter() - started) * 1000
    if remote.is_error:
        raise HTTPException(status_code=remote.status_code, detail=remote.text)

    data = remote.json()
    required = {
        "document_id",
        "version",
        "exists",
        "size_bytes",
        "sha256",
        "node",
        "processing_time_ms",
    }
    if not isinstance(data, dict) or not required.issubset(data):
        raise HTTPException(status_code=502, detail="Incomplete storage response")
    return {
        "service_response": data,
        "metrics": {
            "request_body_bytes": 0,
            "response_body_bytes": len(remote.content),
            "latency_ms": latency_ms,
        },
    }


@app.post("/documents/info/no-schema")
async def get_info_from_json_without_schema(request: Request):
    try:
        request_data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Malformed JSON") from None

    # These are manual checks rather than a predefined JSON Schema.
    if not isinstance(request_data, dict):
        raise HTTPException(status_code=400, detail="JSON object expected")
    if "document_id" not in request_data or "version" not in request_data:
        raise HTTPException(
            status_code=400,
            detail="document_id and version are required",
        )

    encoded_request = compact_json(request_data)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            remote = await client.post(
                f"{STORAGE_URL}/no-schema/files/info",
                content=encoded_request,
                headers={"Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Storage node unavailable: {exc}",
        ) from None
    latency_ms = (time.perf_counter() - started) * 1000

    if remote.is_error:
        raise HTTPException(status_code=remote.status_code, detail=remote.text)
    try:
        response_data = remote.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Storage node returned malformed JSON",
        ) from None

    required = {
        "document_id",
        "version",
        "exists",
        "size_bytes",
        "sha256",
        "node",
        "processing_time_ms",
    }
    if not isinstance(response_data, dict) or not required.issubset(response_data):
        raise HTTPException(status_code=502, detail="Incomplete storage response")

    return {
        "service_response": response_data,
        "metrics": {
            "request_body_bytes": len(encoded_request),
            "response_body_bytes": len(remote.content),
            "latency_ms": latency_ms,
        },
    }
