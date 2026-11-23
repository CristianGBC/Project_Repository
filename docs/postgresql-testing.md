# PostgreSQL aislado para pruebas

La suite de integración modifica y trunca datos. Debe ejecutarse únicamente
contra una base desechable cuyo nombre termine en `_test`. Nunca uses aquí
`DATABASE_URL` de desarrollo, QA o producción.

## Instancia reproducible con Docker

El archivo `infrastructure/docker/compose.test.yaml` levanta PostgreSQL 16 en
un proyecto, contenedor, puerto y volumen separados de los perfiles
`coordinator` y `storage`. Solo publica el puerto en la interfaz local.

Inicia la instancia desde la raíz del repositorio:

```bash
docker compose -f infrastructure/docker/compose.test.yaml up -d --wait
```

Los valores predeterminados son deliberadamente locales y exclusivos para
pruebas:

```text
base:    yachayrepo_test
usuario: yachayrepo_test
clave:   yachayrepo_test
puerto:  55432
```

Pueden sustituirse sin modificar archivos versionados:

```bash
TEST_POSTGRES_DB=my_catalog_test \
TEST_POSTGRES_USER=my_test_user \
TEST_POSTGRES_PASSWORD='LOCAL_TEST_PASSWORD' \
TEST_POSTGRES_PORT=55432 \
  docker compose -f infrastructure/docker/compose.test.yaml up -d --wait
```

`TEST_POSTGRES_DB` debe terminar en `_test`, porque pytest rechaza cualquier
otro nombre antes de ejecutar operaciones destructivas. Si cambias las
credenciales, utiliza los mismos valores al construir la URL de las pruebas.

## Migraciones y carga inicial

Exporta URLs dirigidas únicamente a la instancia anterior:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://yachayrepo_test:yachayrepo_test@localhost:55432/yachayrepo_test'
export DATABASE_URL="$TEST_DATABASE_URL"
```

Comprueba y aplica las tres migraciones sobre una base vacía:

```bash
.venv/bin/python -m alembic -c backend/alembic.ini current
.venv/bin/python -m alembic -c backend/alembic.ini upgrade head
.venv/bin/python -m alembic -c backend/alembic.ini current
```

La revisión final esperada es `20261116_03`. La carga institucional puede
ejecutarse repetidamente; usa `ON CONFLICT DO NOTHING` y no debe duplicar
escuelas, tipos, hosts ni nodos:

```bash
.venv/bin/python -m backend.app.seed
.venv/bin/python -m backend.app.seed
```

## Pruebas de integración y suite completa

No basta con configurar `DATABASE_URL`: los fixtures habilitan PostgreSQL solo
cuando existe `TEST_DATABASE_URL` y vuelven a validar el sufijo `_test`.

Ejecuta únicamente los casos que usan PostgreSQL:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  .venv/bin/python -m pytest \
  backend/tests/test_database.py \
  backend/tests/test_document_catalog_integration.py -q
```

Ejecuta toda la suite:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  .venv/bin/python -m pytest backend/tests -q
```

Sin `TEST_DATABASE_URL`, los casos PostgreSQL aparecen como `skipped`; las
pruebas HTTP y de filesystem continúan ejecutándose. Un resultado sin casos
omitidos confirma que las integraciones se ejecutaron realmente.

## Detención y limpieza segura

Detén únicamente PostgreSQL de pruebas, conservando sus datos:

```bash
docker compose -f infrastructure/docker/compose.test.yaml down
```

Cuando quieras reconstruir la base de pruebas desde cero, elimina solamente su
volumen dedicado:

```bash
docker compose -f infrastructure/docker/compose.test.yaml down -v
```

Este último comando no referencia `infrastructure/docker/compose.yaml` y no
afecta los seis volúmenes documentales. Antes de ejecutarlo, confirma que el
comando contiene exactamente `compose.test.yaml`.
