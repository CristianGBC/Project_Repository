# Replicación síncrona RF = 3

YachayRepo admite factores de replicación `1` y `3` para nuevas operaciones
respaldadas por el catálogo PostgreSQL. RF = 3 es el valor predeterminado; RF =
1 se conserva para experimentos comparativos.

```dotenv
REPLICATION_FACTOR=3
```

El coordinador rechaza cualquier valor distinto de `1` o `3` durante el inicio.
El modo de compatibilidad sin `DATABASE_URL` continúa atendiendo documentos
Stage 3 en un solo nodo y no migra ni replica automáticamente archivos antiguos.

## Selección y anticolocación

El selector consulta `storage_nodes` y `physical_hosts` en PostgreSQL. Considera
únicamente nodos configurados, activos, no marcados `down` y ubicados en hosts
activos. Ordena primero el nodo cuya `home_school_id` coincide con la escuela y
después por identificador, para producir resultados deterministas. Finalmente
elige como máximo un nodo por `physical_host_id`.

La topología que registra actualmente el seed es:

| Host físico | Nodos lógicos |
|---|---|
| `raspberry-1` | `node-bio`, `node-phy` |
| `raspberry-2` | `node-math` |
| `raspberry-3` | `node-chem`, `node-earth` |
| `raspberry-4` | `node-agro` |

Esta distribución difiere del plan que coloca `node-chem` en Raspberry Pi 2.
El catálogo es la fuente de verdad y no se cambió silenciosamente. Para una
carga de Matemáticas con el seed actual, la selección determinista es
`node-math`/`raspberry-2`, `node-agro`/`raspberry-4` y
`node-bio`/`raspberry-1`.

Las restricciones `uq_replica_version_host` y `uq_replica_version_node`
refuerzan en PostgreSQL que una versión no repita host físico ni nodo. Si no hay
suficientes hosts elegibles, la transacción inicial se revierte y no se publica
el documento o versión.

## Flujo de escritura

1. El coordinador valida el archivo, limita su tamaño y calcula SHA-256.
2. Una transacción corta crea el documento o versión y exactamente
   `target_replicas` filas `pending` en hosts distintos.
3. La transacción termina antes de cualquier HTTP.
4. Se crean cursores temporales independientes con los mismos bytes; no se
   comparte un cursor mutable entre tareas.
5. Las transferencias se ejecutan concurrentemente con HTTPX.
6. Cada respuesta se valida individualmente por identidad, tamaño y SHA-256.
7. Cada réplica exitosa pasa a `healthy`; cada fallo queda como `failed` con su
   error e intento.
8. La versión pasa a `available` y puede convertirse en la actual únicamente
   cuando todas las réplicas esperadas están `healthy` en hosts distintos.

Si una de tres transferencias falla, la versión queda `failed`, las copias
correctas se conservan y una versión actual anterior no cambia. No hay limpieza
automática: esas filas son evidencia para la futura reconciliación.

Cada versión usa su propio conjunto de réplicas. La versión 1 conserva por
compatibilidad el UUID del documento como clave física; versiones posteriores
usan el UUID de la versión, por lo que nunca sobrescriben contenido anterior.

## Lecturas y limitaciones actuales

La descarga ordena las réplicas `healthy`, prioriza el nodo de la escuela y
verifica nuevamente tamaño y SHA-256. Si una copia falla, intenta la siguiente
automáticamente como se explica en
[`read-failover.md`](read-failover.md). Todavía no incluye monitoreo, reparación,
reintentos persistentes ni recuperación automática. PostgreSQL sigue siendo un
punto único de fallo.

## Prueba automatizada

Inicia PostgreSQL de pruebas y ejecuta toda la suite:

```bash
docker compose -f infrastructure/docker/compose.test.yaml up -d --wait
TEST_DATABASE_URL='postgresql+psycopg://yachayrepo_test:yachayrepo_test@localhost:55432/yachayrepo_test' \
  .venv/bin/python -m pytest backend/tests -q
docker compose -f infrastructure/docker/compose.test.yaml down
```

Las pruebas cubren RF = 1, RF = 3, anticolocación, prioridad de escuela,
insuficiencia de hosts, estados independientes, bytes y hashes idénticos,
fallos parciales, concurrencia, historial, borrado lógico y compatibilidad
legacy.

## Prueba Docker aislada

`compose.rf3-test.yaml` utiliza puertos `18000–18006`, PostgreSQL `55433` y
volúmenes exclusivos. No toca los contenedores ni volúmenes normales.

```bash
docker compose -f infrastructure/docker/compose.rf3-test.yaml \
  up --build -d --wait

DATABASE_URL='postgresql+psycopg://yachayrepo_rf3_test:yachayrepo_rf3_test@localhost:55433/yachayrepo_rf3_test' \
  .venv/bin/python -m alembic -c backend/alembic.ini upgrade head
DATABASE_URL='postgresql+psycopg://yachayrepo_rf3_test:yachayrepo_rf3_test@localhost:55433/yachayrepo_rf3_test' \
  .venv/bin/python -m backend.app.seed

curl -X POST http://127.0.0.1:18000/documents \
  -F 'school_id=school-math' \
  -F 'file=@docs/rf3-replication.md;type=text/markdown'
```

Consulta `document_replicas` para obtener los tres nodos. Sus puertos de prueba
son `18001–18006`; `GET /files/{storage_id}` permite comparar cada SHA-256. Para
la versión 1, `storage_id` es el `document_id`.

Detén el entorno conservando los recursos aislados:

```bash
docker compose -f infrastructure/docker/compose.rf3-test.yaml down
```

Solo si se desea eliminar los datos desechables de esta prueba, usa `down -v`
con ese archivo exacto. Nunca lo ejecutes contra `compose.yaml`.

Al trasladar nodos a Raspberry Pi no cambia el selector: únicamente cambian las
URLs configurables. La política continúa leyendo la colocación física desde
PostgreSQL.
