# Workshop 7 — JSON data interchange

This prototype extends the distributed institutional repository from Workshop 6.
It implements two HTTP services between a coordinator and a storage node:

1. `POST /schema/files`: stores a Base64-encoded document using predefined JSON
   request and response schemas.
2. `POST /no-schema/files/info`: receives a JSON request and returns replica
   information using manual checks without a predefined schema.

The components must run on different physical machines or virtual machines to
satisfy the workshop requirement. Use only non-sensitive test documents.

## Machine B: storage node

From `repository-workshop-json`:

```bash
docker build -t workshop7-json .
docker run --rm --name workshop7-storage \
  -p 8001:8000 \
  -e NODE_NAME=school-of-computing \
  -v workshop7_storage:/data \
  workshop7-json \
  uvicorn storage.main:app --host 0.0.0.0 --port 8000
```

Allow TCP port `8001` only on the local laboratory network. Verify locally:

```bash
curl http://localhost:8001/health
```

## Machine A: coordinator

Replace `192.168.1.11` with Machine B's private IP:

```bash
docker build -t workshop7-json .
docker run --rm --name workshop7-coordinator \
  -p 8000:8000 \
  -e STORAGE_URL=http://192.168.1.11:8001 \
  workshop7-json \
  uvicorn coordinator.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs`, execute `POST /documents/schema`, and copy the
returned `document_id`. Then execute `POST /documents/info/no-schema` with:

```json
{
  "document_id": "the-generated-document-id",
  "version": 1
}
```

## Automated measurements

The measurement client should run on Machine A. Install the dependencies or use
the image, then pass a small test file and Machine B's URL:

```bash
python3 -m pip install -r requirements.txt
python3 experiments/measure.py sample.pdf \
  --url http://192.168.1.11:8001 \
  --repetitions 20
```

The first request is discarded as a warm-up. The output reports body bytes,
latency, server processing time, and JSON Schema validation time. Body-byte
measurements exclude HTTP headers and TCP/IP overhead.

To save a result for the report:

```bash
python3 experiments/measure.py sample.pdf \
  --url http://192.168.1.11:8001 \
  --repetitions 20 | tee results-1kb.json
```

Repeat with files of approximately 1 KB, 10 KB, 100 KB, and 1 MB. Keep both
machines and the network conditions unchanged.

## Negative validation tests

Send a request missing `sha256`:

```bash
curl -i -X POST http://192.168.1.11:8001/schema/files \
  -H 'Content-Type: application/json' \
  -d '{"document_id":"test1","version":1,"filename":"test.txt","content_base64":"SGVsbG8="}'
```

Expected result: HTTP `422`. Also test a wrong checksum (HTTP `400`) and an
invalid version type (HTTP `422`).

## Important interpretation

Base64 increases the document representation size. The two workshop services
also perform different operations, so their total latencies do not isolate only
the cost of schema validation. Use the separately reported validation times to
discuss that cost accurately.
