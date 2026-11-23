# Failover automático de lectura

YachayRepo intenta automáticamente réplicas alternativas de la misma versión
cuando la primera copia no puede descargarse o no supera la verificación de
integridad. El comportamiento funciona con versiones RF = 1 y RF = 3 y no
depende del factor configurado actualmente para nuevas cargas.

## Selección de candidatos

El coordinador consulta `document_replicas`, `storage_nodes` y
`physical_hosts`. Solo considera réplicas `healthy` en nodos y hosts activos,
no marcados `down` y presentes en la configuración del coordinador. Los
candidatos pertenecen exclusivamente al `version_id` solicitado.

El orden es determinista:

1. nodo cuya `home_school_id` coincide con la escuela del documento;
2. resto de nodos por identificador.

Con la topología sembrada, una versión de Matemáticas normalmente intenta
`node-math`, después `node-agro` y finalmente `node-bio`.

## Verificación antes de responder

Cada intento se descarga por bloques a un archivo temporal independiente. El
coordinador comprueba el número total de bytes y calcula SHA-256 contra
`document_versions.size_bytes` y `document_versions.sha256`. Solo después de
validar el archivo completo crea la respuesta streaming para el cliente.

Los bytes de una copia corrupta o incompleta nunca se envían parcialmente al
usuario. Los temporales se cierran tanto al fallar como cuando termina o se
cancela la respuesta. Solicitudes concurrentes no comparten archivos ni
cursores.

## Clasificación y estado

| Observación | Tipo interno | Actualización conservadora |
|---|---|---|
| Conexión rechazada, nodo apagado o timeout | `unreachable` | Conserva `healthy`; registra `last_error` |
| HTTP 5xx | `server_error` | Conserva `healthy`; registra `last_error` |
| Transferencia interrumpida | `interrupted` | Conserva `healthy`; registra `last_error` |
| HTTP 404 confirmado | `missing` | Cambia solamente esa réplica a `missing` |
| Tamaño, SHA-256 o integridad del nodo incorrectos | `corrupted` | Cambia solamente esa réplica a `corrupted` |

Una lectura correcta actualiza `verified_at` y limpia el último error de esa
réplica. Todas las actualizaciones usan transacciones cortas posteriores al
HTTP. La versión permanece `available` y `current_version_id` no cambia por el
fallo de una copia.

Los logs incluyen documento, versión, réplica, nodo, número de intento, tipo de
fallo, duración aproximada y copia finalmente utilizada. Nunca incluyen bytes,
contraseñas ni URLs con credenciales.

## Respuestas

- Si una réplica falla y otra es íntegra, la descarga responde normalmente.
- Si todas fallan temporalmente, responde HTTP 503 con un mensaje genérico.
- Si todas las candidatas están ausentes o corruptas, responde HTTP 409.
- Documento, versión inexistente y borrado lógico conservan sus respuestas
  anteriores.
- Los documentos legacy continúan usando su flujo compatible de un solo nodo.

## Prueba automatizada

```bash
docker compose -f infrastructure/docker/compose.test.yaml up -d --wait
TEST_DATABASE_URL='postgresql+psycopg://yachayrepo_test:yachayrepo_test@localhost:55432/yachayrepo_test' \
  .venv/bin/python -m pytest backend/tests -q
docker compose -f infrastructure/docker/compose.test.yaml down
```

La suite simula conexión, timeout, HTTP 500, ausencia, corrupción, respuesta
corta e interrupción; también comprueba dos fallos, fallo total, RF = 1,
versiones históricas, borrado lógico, legacy, temporales y concurrencia.

## Experimento Docker aislado

Inicia y prepara `compose.rf3-test.yaml` como se explica en
[`rf3-replication.md`](rf3-replication.md), carga un documento de Matemáticas y
conserva su `DOCUMENT_ID`.

Descarga con todas las copias activas:

```bash
curl -fsS \
  http://127.0.0.1:18000/documents/school-math/DOCUMENT_ID/download \
  | sha256sum
```

Detén la primera réplica y repite; el coordinador debe usar `node-agro`:

```bash
docker compose -f infrastructure/docker/compose.rf3-test.yaml stop node-math-test
```

Detén también la segunda; debe usar `node-bio`:

```bash
docker compose -f infrastructure/docker/compose.rf3-test.yaml stop node-agro-test
```

Al detener la tercera, la descarga debe responder HTTP 503:

```bash
docker compose -f infrastructure/docker/compose.rf3-test.yaml stop node-bio-test
curl -i http://127.0.0.1:18000/documents/school-math/DOCUMENT_ID/download
```

Restaura las tres y verifica nuevamente los hashes:

```bash
docker compose -f infrastructure/docker/compose.rf3-test.yaml \
  start node-math-test node-agro-test node-bio-test
docker compose -f infrastructure/docker/compose.rf3-test.yaml up -d --wait
```

Estos fallos temporales no cambian las réplicas a `corrupted` ni eliminan
archivos. El monitoreo periódico puede activarse con el perfil `monitor`, como
explica [`health-monitoring.md`](health-monitoring.md), pero no repara copias.

## Limitaciones

- No se recrean copias `missing` o `corrupted`.
- No hay reconciliación de réplicas fallidas.
- La salud es periódica y puede quedar desactualizada hasta el próximo ciclo y
  sus umbrales.
- PostgreSQL continúa siendo un punto único de fallo.
