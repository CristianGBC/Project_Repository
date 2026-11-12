# Workshop 6 — Functional distributed repository prototype

FastAPI REST gateway + six independent storage containers + RabbitMQ fanout events + audit worker.
This is a **teaching prototype**, not a production document management system.

## Run

```bash
docker compose up --build -d
docker compose ps
```

- API Swagger: http://localhost:8000/docs
- RabbitMQ UI: http://localhost:15674 (guest / guest)
- Uses internal Docker RabbitMQ networking; **does not conflict** with your existing RabbitMQ on ports 5673/15673.

## Tests

1. `curl http://localhost:8000/nodes` — expect six available nodes.
2. Create a document using Swagger `POST /documents` with a small test file. Copy `document_id`.
3. `GET /documents/{doc}/versions` — expect version 1, three replica nodes.
4. `GET /documents/{doc}` — download the latest document.
5. Update using `PUT /documents/{doc}` with new content three times (versions 2, 3, 4).
6. `GET /documents/{doc}/versions` — expect versions 4, 3, 2 (the oldest is removed after successful cleanup).
7. `docker compose logs worker` — expect `document.created` and `document.updated` events.
8. Stop one node containing the latest version, e.g. `docker compose stop node1`, and download again; it should use another replica. Then `docker compose start node1`.

To see queues and exchanges: RabbitMQ UI → Exchanges → `repository.events`; Queues → `repository.audit`.

## Limitations and next iteration

- Node failure **detection and failover reads** are supported, but **automatic replica repair is not implemented** in this prototype.
- Retention cleanup is best effort; failed deletes remain in metadata for later cleanup.
- The coordinator uses SQLite and an in-process lock, appropriate for a single API instance only.
- `school` is accepted for demonstration but not yet persisted or used for school-first replica placement.
- Authentication, permissions, upload limits, persistent retries, and robust transactional event delivery are not production ready.
- RabbitMQ events are durable but outbox publication uses best-effort marking, so consumers must tolerate duplicates.
- Uploads use a sequential three-replica confirmation before publishing metadata.
- Run on local test files only; do not upload sensitive institutional records.
