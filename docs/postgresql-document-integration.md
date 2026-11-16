# PostgreSQL document integration (Stage 4.3)

The coordinator on Host A stores document bytes on the configured storage
nodes and treats the PostgreSQL catalog on Host B as the primary metadata
source. PostgreSQL never stores file contents. Existing per-node JSON remains
internal metadata and a read fallback for Stage 3 documents not yet imported.

## Configuration

The coordinator profile receives these values from the untracked
`infrastructure/docker/.env`:

```text
DATABASE_URL=postgresql+psycopg://yachayrepo_app:URL_ENCODED_PASSWORD@POSTGRES_HOST:5432/yachayrepo_db?sslmode=require
TECHNICAL_USER_ID=7d14ef5d-3c61-4ac8-90b5-2bd3f0aa0431
TECHNICAL_USER_EMAIL=development-operations@yachayrepo.invalid
TECHNICAL_USER_PASSWORD_HASH=!authentication-disabled!
```

The URL must use Psycopg 3 and preserve the server's required TLS option. Never
commit the real host or password. `NODE_*_URL` remains the source of truth for
node addresses; database host IPs are not used to construct storage URLs.

The stable technical user is created idempotently on the first catalog-backed
upload. It is non-admin and has a disabled-login marker or an operator-supplied
password hash. It exists only for authorship foreign keys until authentication
replaces it; this stage adds no login, JWT, or authentication endpoint.

## Identifier resolution and node selection

The API accepts transitional `node-math` and canonical `school-math` inputs.
They are resolved explicitly; schools and nodes are never treated as the same
entity. Selection considers active configured nodes not marked `down` and
currently prefers the school's home node. It is not restricted to that node,
so later RF=3 selection can spread replicas across physical hosts.

## Upload flow

`POST /documents` keeps multipart fields `file` and `school_id` and optionally
accepts `title` and `description`.

1. The coordinator validates and spools the file while calculating SHA-256.
2. A short transaction validates the school, creates the technical user if
   missing, and inserts a document, version 1 in `pending`, and one pending
   replica. The application sets `target_replicas=1`; the schema default stays
   at 3 for the future replication stage.
3. The transaction commits before the HTTP upload begins.
4. The node validates size and SHA-256 and atomically publishes bytes and JSON.
5. A second short transaction marks the replica `healthy`, records storage and
   verification times, marks the version `available`, and assigns the current
   version.

Confirmed versions are immutable. Stage 4.3 creates version 1; Stage 4.4 adds
new-version and soft-delete operations documented in
[`document-versioning-soft-delete.md`](document-versioning-soft-delete.md).

If storage fails, replica and version become `failed` and the document has no
current version. A process interruption leaves discoverable `pending` rows. If
the file is stored but final catalog confirmation fails, the API returns 503
with the document ID and does not delete the possibly durable file. Automatic
retry, orphan cleanup, and reconciliation are deferred.

## Metadata and download

`GET /documents/{school_id}/{document_id}` reads document, current version,
school, and replica from PostgreSQL. Soft-deleted documents are excluded. The
response keeps Stage 3 fields and adds title, description, version ID, number,
status, and target replica count.

Only a genuine catalog miss falls back to old node JSON. Database errors return
503, soft-deleted rows return 404, and incomplete operations return 409; these
conditions never fall back. A future import job should scan JSON, validate
bytes, and create catalog rows before removing compatibility reads.

`GET /documents/{school_id}/{document_id}/download` resolves the healthy
replica, maps its node ID to `NODE_*_URL`, downloads, and rechecks size and
SHA-256. A missing registered file produces 409 and marks the replica `missing`
when PostgreSQL is writable.

## Prepare and inspect PostgreSQL safely

Inspect the remote revision before changing it:

```bash
cd backend
DATABASE_URL="$DATABASE_URL" alembic current
DATABASE_URL="$DATABASE_URL" alembic history
```

After reviewing the target and backup, apply the existing migration and seed
explicitly. Stage 4.3 adds no migration because the eight-table schema already
supports this workflow.

```bash
DATABASE_URL="$DATABASE_URL" alembic upgrade head
cd ..
DATABASE_URL="$DATABASE_URL" python -m backend.app.seed
```

Inspect catalog records without exposing credentials:

```bash
psql "$DATABASE_URL" -c "SELECT id, school_id, current_version_id, deleted_at FROM documents ORDER BY created_at DESC LIMIT 10;"
psql "$DATABASE_URL" -c "SELECT document_id, version_number, status, target_replicas, sha256 FROM document_versions ORDER BY created_at DESC LIMIT 10;"
psql "$DATABASE_URL" -c "SELECT version_id, node_id, physical_host_id, status, stored_at, verified_at FROM document_replicas ORDER BY created_at DESC LIMIT 10;"
```

## Run and test

Start services without deleting existing containers or volumes:

```bash
docker compose --env-file infrastructure/docker/.env \
  -f infrastructure/docker/compose.yaml \
  --profile storage --profile coordinator up --build -d
```

Use only a disposable database whose name ends in `_test`; the suite rejects
other names:

```bash
.venv/bin/pip install -r backend/requirements-dev.txt
TEST_DATABASE_URL='postgresql+psycopg://.../yachayrepo_test?sslmode=require' \
  .venv/bin/python -m pytest backend/tests -q
```

Without `TEST_DATABASE_URL`, PostgreSQL cases skip and API/filesystem tests run.

## Current limitations

- RF is fixed at 1; there is no repair worker or cross-host RF=3.
- PostgreSQL remains a single point of failure.
- Authentication, authorization, permanent purge, and automatic recovery are
  not implemented. Soft deletion and immutable later versions are available
  from Stage 4.4.
- Legacy JSON documents are read-compatible but not automatically imported.
- SQLAlchemy calls are synchronous but transactions are deliberately short; a
  later high-throughput stage may use an async driver or worker.
