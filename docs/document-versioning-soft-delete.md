# Document versioning and soft deletion (Stage 4.4)

Stage 4.4 adds immutable version history and reversible logical deletion using
the existing eight-table PostgreSQL schema. No Alembic migration is required.

## Consistency model

Creating a version locks its `documents` row while calculating the next
`version_number`. Concurrent requests therefore receive unique, monotonic
numbers and still satisfy the existing `(document_id, version_number)` unique
constraint. The database transaction commits before HTTP storage begins.

Version 1 keeps the Stage 4.3 physical object key (`documents.id`). Versions 2
and later use their own `document_versions.id` as the node object key, so they
cannot overwrite one another. All versions still reference the same logical
document in PostgreSQL.

A new version starts as `pending`; only a matching storage confirmation changes
its replica to `healthy` and the version to `available`. A failed upload leaves
the prior current version unchanged. Concurrent confirmations update
`current_version_id` only when the confirmed version number is newer, preventing
a slower version 2 request from replacing an already confirmed version 3.

Soft deletion sets `documents.deleted_at` and `deleted_by` to the configured
technical user. It never deletes versions, replicas, JSON, bytes, or Docker
volumes. Restore clears both deletion fields as required by the schema. The
schema has no `restored_by` audit column, so this stage avoids a schema change;
the technical identity is resolved during restore but only deletion authorship
is durable. A future audit-log stage should record restoration actors.

## API

- `POST /documents/{document_id}/versions`: store a new immutable version.
- `GET /documents/{document_id}/versions`: list newest version first.
- `GET /documents/{document_id}/versions/{version_number}/download`: download
  and verify one historical version.
- `DELETE /documents/{document_id}`: logically delete; repeated calls return
  `already_deleted`.
- `POST /documents/{document_id}/restore`: restore; repeated calls return
  `already_active`.

The existing school-scoped metadata and download endpoints continue resolving
the current version. Deleted documents return 404 from normal metadata,
history, current download, and historical download operations.

## Run the updated coordinator

Do not remove existing volumes. Rebuild and start the current services with:

```bash
docker compose --env-file infrastructure/docker/.env \
  -f infrastructure/docker/compose.yaml \
  --profile storage --profile coordinator up --build -d
```

Open <http://localhost:8000/docs> after the coordinator health check succeeds.

## Manual Swagger walkthrough

Use two different small files so their checksums are visibly different, for
example `version-1.txt` containing `first version` and `version-2.txt`
containing `second version`.

### 1. Create a document

Open `POST /documents`, select **Try it out**, and enter:

- `school_id`: `school-math`
- `title`: `Swagger versioning test`
- `description`: `Manual Stage 4.4 verification`
- `file`: `version-1.txt`

Execute. Expect HTTP 201 with `version_number: 1`,
`version_status: "available"`, `target_replicas: 1`, and `status: "stored"`.
Copy `document_id` from the JSON response; all following examples call it
`DOCUMENT_ID`.

### 2. Upload version 2

Open `POST /documents/{document_id}/versions` and enter:

- `document_id`: the copied `DOCUMENT_ID`
- `file`: `version-2.txt`

Expect HTTP 201, the same `document_id`, a new `version_id`, and
`version_number: 2` with `version_status: "available"`.

### 3. Inspect history

Call `GET /documents/{document_id}/versions` with `DOCUMENT_ID`. Expect two
items ordered `[2, 1]`. Version 2 must have `is_current: true`; version 1 must
remain present with `is_current: false`. Record both SHA-256 values.

### 4. Download versions 1 and 2

Call these operations separately:

```text
GET /documents/DOCUMENT_ID/versions/1/download
GET /documents/DOCUMENT_ID/versions/2/download
```

Use Swagger's **Download file** link and save them under different names. On
the Dell, compare the originals and downloads:

```bash
sha256sum version-1.txt downloaded-version-1.txt
sha256sum version-2.txt downloaded-version-2.txt
```

Each pair must match, and each value must equal the corresponding history row.
The existing `GET /documents/school-math/DOCUMENT_ID/download` must return
version 2 because it is current.

### 5. Soft-delete

Call `DELETE /documents/{document_id}`. Expect HTTP 200 with
`status: "deleted"` and a non-null `deleted_at`. A repeated call returns
`status: "already_deleted"` without changing files.

While deleted, these normal calls must return 404:

```text
GET /documents/school-math/DOCUMENT_ID
GET /documents/school-math/DOCUMENT_ID/download
GET /documents/DOCUMENT_ID/versions
GET /documents/DOCUMENT_ID/versions/1/download
```

Run the SQL below: `deleted_at` and `deleted_by` must be non-null, while both
version and replica rows remain. The node files also remain; after restoration,
both downloads prove that no physical deletion occurred.

### 6. Restore

Call `POST /documents/{document_id}/restore`. Expect HTTP 200 with
`status: "restored"` and `deleted_at: null`. A repeated call returns
`status: "already_active"`. Repeat the history and both download requests;
both versions must be available and unchanged.

## Exact SQL verification

Replace `DOCUMENT_ID` with the UUID copied from Swagger. With `psql`, UUID
variables avoid repeating it:

```sql
\set document_id 'DOCUMENT_ID'

SELECT
    id,
    title,
    school_id,
    current_version_id,
    deleted_at,
    deleted_by,
    created_at,
    updated_at
FROM documents
WHERE id = :'document_id'::uuid;

SELECT
    id AS version_id,
    document_id,
    version_number,
    filename,
    mime_type,
    size_bytes,
    sha256,
    status,
    target_replicas,
    committed_at,
    created_at,
    (id = (SELECT current_version_id FROM documents
           WHERE id = :'document_id'::uuid)) AS is_current
FROM document_versions
WHERE document_id = :'document_id'::uuid
ORDER BY version_number DESC;

SELECT
    r.id AS replica_id,
    r.version_id,
    v.version_number,
    r.node_id,
    r.physical_host_id,
    r.status,
    r.attempts,
    r.stored_at,
    r.verified_at,
    r.last_error
FROM document_replicas AS r
JOIN document_versions AS v ON v.id = r.version_id
WHERE v.document_id = :'document_id'::uuid
ORDER BY v.version_number DESC;
```

Run them through the configured URL without writing credentials in shell
history when possible:

```bash
psql "$DATABASE_URL"
```

## Automated tests

Use only a disposable database whose name ends in `_test`:

```bash
TEST_DATABASE_URL='postgresql+psycopg://.../yachayrepo_stage44_test' \
  .venv/bin/python -m pytest backend/tests -q
```

Tests cover sequential versions, historical integrity, failed uploads,
concurrent numbering, current-version ordering, soft deletion, repeated delete
and restore, read restrictions, physical preservation, and Stage 4.3
compatibility.

## Deferred work

- RF=3 placement and replica repair.
- Automatic reconciliation of `pending`/failed transfers.
- Authentication and per-school authorization.
- Durable audit events for restore and other lifecycle operations.
- Permanent purge and retention policy.
