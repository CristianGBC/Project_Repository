# PostgreSQL catalog (Stage 4.2)

PostgreSQL is an independent catalog service on Host B. The FastAPI coordinator
on Host A is its client; the six storage nodes continue storing files and JSON
metadata exactly as in Stage 3. PostgreSQL is initially a **single point of
failure**. This stage does not add database failover, file replication, login,
or migration of existing JSON metadata.

## Schema

| Table | Purpose |
|---|---|
| `schools` | Six institutional schools. |
| `users` | Future identities; email uniqueness is enforced on `lower(email)`. |
| `school_memberships` | School affiliation and system role as separate fields. |
| `documents` | Logical documents, visibility, current version, and soft deletion. |
| `document_versions` | Immutable version metadata and SHA-256 integrity data. |
| `physical_hosts` | Physical Raspberry Pi devices, without hard-coded IP addresses. |
| `storage_nodes` | Logical nodes and their physical placement. |
| `document_replicas` | Future replica state and host-level anti-colocation rules. |

`documents.current_version_id` uses the composite foreign key
`(current_version_id, documents.id) -> (document_versions.id,
document_versions.document_id)`. Therefore a current version cannot belong to a
different document. Alembic creates this constraint after both sides of the
circular relationship exist. A replica references `(storage_nodes.id,
storage_nodes.physical_host_id)` and is unique per version and physical host.

## Native PostgreSQL on Host B

First inspect the existing installation; do not install a second server:

```bash
sudo systemctl status postgresql
sudo -u postgres psql -c 'SELECT version();'
sudo -u postgres psql -Atc 'SHOW port; SHOW config_file; SHOW hba_file;'
ss -ltn | grep 5432
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
```

The last command determines whether an existing Docker server owns the port.
The following provisioning commands create new objects only; choose a strong
password interactively and first confirm that the names do not already exist:

```bash
sudo -u postgres psql -c "SELECT rolname FROM pg_roles WHERE rolname = 'yachayrepo_app';"
sudo -u postgres psql -c "SELECT datname FROM pg_database WHERE datname = 'yachayrepo_db';"
sudo -u postgres createuser --pwprompt yachayrepo_app
sudo -u postgres createdb --owner=yachayrepo_app yachayrepo_db
```

Do not run the creation commands if the checks find existing objects until their
owner and purpose have been confirmed.

For a remote coordinator, find `config_file` and `hba_file` with the query
above. With administrator approval, set `listen_addresses` to Host B's LAN
address (not `*`) in `postgresql.conf`. Add one narrow rule to `pg_hba.conf`,
replacing `HOST_A_IP/32`:

```text
hostssl yachayrepo_db yachayrepo_app HOST_A_IP/32 scram-sha-256
```

Use `host` only on a trusted isolated lab network when TLS is not configured.
Restrict the host firewall to the coordinator:

```bash
sudo ufw allow from HOST_A_IP to any port 5432 proto tcp
sudo systemctl reload postgresql
```

These commands require administrator privileges and change server-wide network
exposure. Review the resolved paths, subnet, TLS policy, and firewall before
running them. Never open `5432` to `0.0.0.0/0`.

## Coordinator configuration and migrations

Create an untracked `.env` from `infrastructure/docker/.env.example` and set:

```text
DATABASE_URL=postgresql+psycopg://yachayrepo_app:URL_ENCODED_PASSWORD@HOST_B:5432/yachayrepo_db
```

From the project root, verify connectivity and run the migration:

```bash
psql "postgresql://yachayrepo_app@HOST_B:5432/yachayrepo_db" -c 'SELECT current_database(), current_user;'
cd backend
DATABASE_URL='postgresql+psycopg://...' alembic current
DATABASE_URL='postgresql+psycopg://...' alembic upgrade head
cd ..
```

Alembic is the only schema-creation mechanism. It does not use
`Base.metadata.create_all()`. Before applying to a nonempty database, inspect it
and take a backup; the initial migration assumes these eight table names are not
already present.

Confirm the revision and tables:

```bash
psql "$DATABASE_URL" -c 'SELECT version_num FROM alembic_version;'
psql "$DATABASE_URL" -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;"
```

## Initial data

The seed is idempotent and uses `ON CONFLICT DO NOTHING`, preserving existing
operator-managed records. Hostnames can be changed through
`RASPBERRY_1_HOSTNAME` through `RASPBERRY_4_HOSTNAME`; IP addresses remain
`NULL`.

```bash
DATABASE_URL='postgresql+psycopg://...' python -m backend.app.seed
```

It registers six schools, four physical hosts, and the existing logical nodes
on ports `8001` through `8006`. It creates no users.

## Isolated database tests

Create a disposable database whose name ends in `_test`. Never reuse QA or the
application database:

```bash
sudo -u postgres createdb --owner=yachayrepo_app yachayrepo_test
TEST_DATABASE_URL='postgresql+psycopg://yachayrepo_app:...@localhost:5432/yachayrepo_test' \
  python -m pytest backend/tests
```

Without `TEST_DATABASE_URL`, PostgreSQL integration tests are reported as
skipped while all Stage 3 tests still run.

## Backup and test restore

Create a custom-format backup without embedding a password in the command:

```bash
pg_dump --host=HOST_B --username=yachayrepo_app --format=custom \
  --file=yachayrepo_db_$(date +%F).dump yachayrepo_db
```

Restore only to a new disposable database, never over the active database:

```bash
sudo -u postgres createdb --owner=yachayrepo_app yachayrepo_restore_test
pg_restore --host=HOST_B --username=yachayrepo_app \
  --dbname=yachayrepo_restore_test --exit-on-error yachayrepo_db_DATE.dump
psql --host=HOST_B --username=yachayrepo_app --dbname=yachayrepo_restore_test \
  -c "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1;"
```

## Stage 3 compatibility check

After migration, start the existing profiles and verify unchanged behavior:

```bash
docker compose -f infrastructure/docker/compose.yaml \
  --profile coordinator --profile storage up --build -d
curl http://localhost:8000/health
curl http://localhost:8000/nodes
python -m pytest backend/tests
```

Document upload, metadata lookup, and download remain backed by storage-node
volumes in this stage.
