import argparse
import base64
import hashlib
import json
import statistics
import time
import uuid
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator


BASE_DIR = Path(__file__).resolve().parent.parent
REQUEST_VALIDATOR = Draft202012Validator(
    json.loads((BASE_DIR / "schemas/store_request.schema.json").read_text())
)
RESPONSE_VALIDATOR = Draft202012Validator(
    json.loads((BASE_DIR / "schemas/store_response.schema.json").read_text())
)


def compact_json(data: dict) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def summarize(values: list[float]) -> dict:
    return {
        "mean": statistics.mean(values),
        "minimum": min(values),
        "maximum": max(values),
        "standard_deviation": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def run(base_url: str, file_path: Path, repetitions: int) -> dict:
    payload = file_path.read_bytes()
    document_id = uuid.uuid4().hex
    schema_runs = []
    info_runs = []

    with httpx.Client(timeout=30) as client:
        for index in range(repetitions + 1):
            request_data = {
                "document_id": document_id,
                "version": index + 1,
                "filename": file_path.name,
                "content_base64": base64.b64encode(payload).decode("ascii"),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            validation_started = time.perf_counter()
            REQUEST_VALIDATOR.validate(request_data)
            client_validation_ms = (time.perf_counter() - validation_started) * 1000
            body = compact_json(request_data)

            started = time.perf_counter()
            response = client.post(
                f"{base_url}/schema/files",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            latency_ms = (time.perf_counter() - started) * 1000
            response.raise_for_status()
            response_data = response.json()
            RESPONSE_VALIDATOR.validate(response_data)

            info_request = {
                "document_id": document_id,
                "version": index + 1,
            }
            info_request_body = compact_json(info_request)
            info_started = time.perf_counter()
            info_response = client.post(
                f"{base_url}/no-schema/files/info",
                content=info_request_body,
                headers={"Content-Type": "application/json"},
            )
            info_latency_ms = (time.perf_counter() - info_started) * 1000
            info_response.raise_for_status()
            info_data = info_response.json()

            if index > 0:  # The first request warms up connections and disk access.
                schema_runs.append(
                    {
                        "request_bytes": len(body),
                        "response_bytes": len(response.content),
                        "latency_ms": latency_ms,
                        "client_validation_ms": client_validation_ms,
                        "server_validation_ms": response_data["validation_time_ms"],
                        "server_processing_ms": response_data["processing_time_ms"],
                    }
                )
                info_runs.append(
                    {
                        "request_bytes": len(info_request_body),
                        "response_bytes": len(info_response.content),
                        "latency_ms": info_latency_ms,
                        "server_processing_ms": info_data["processing_time_ms"],
                    }
                )

    def metrics(rows: list[dict], key: str) -> dict:
        return summarize([row[key] for row in rows])

    return {
        "file": str(file_path),
        "original_file_bytes": len(payload),
        "repetitions": repetitions,
        "with_json_schema": {
            "request_body_bytes": schema_runs[0]["request_bytes"],
            "response_body_bytes_mean": metrics(schema_runs, "response_bytes"),
            "latency_ms": metrics(schema_runs, "latency_ms"),
            "client_validation_ms": metrics(schema_runs, "client_validation_ms"),
            "server_validation_ms": metrics(schema_runs, "server_validation_ms"),
            "server_processing_ms": metrics(schema_runs, "server_processing_ms"),
        },
        "without_predefined_schema": {
            "request_body_bytes": info_runs[0]["request_bytes"],
            "response_body_bytes_mean": metrics(info_runs, "response_bytes"),
            "latency_ms": metrics(info_runs, "latency_ms"),
            "server_processing_ms": metrics(info_runs, "server_processing_ms"),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Measure Workshop 7 JSON services")
    parser.add_argument("file", type=Path)
    parser.add_argument("--url", default="http://localhost:8001")
    parser.add_argument("--repetitions", type=int, default=20)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    if not args.file.is_file():
        parser.error(f"File not found: {args.file}")
    print(json.dumps(run(args.url.rstrip("/"), args.file, args.repetitions), indent=2))


if __name__ == "__main__":
    main()
