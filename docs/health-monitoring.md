# Monitoreo periódico de nodos

YachayRepo ejecuta el monitoreo en un proceso independiente del coordinador.
El servicio `health-monitor` consulta concurrentemente `GET /health` en los
seis nodos y persiste sus transiciones en PostgreSQL. Por ello, una caída se
detecta aunque ningún usuario invoque `GET /nodes`.

## Ciclo y validación

Cada ciclo crea como máximo una comprobación por nodo mediante
`asyncio.gather()`. El ciclo siguiente comienza solo cuando terminaron las seis
comprobaciones y la persistencia del ciclo anterior. Una respuesta es saludable
únicamente si es HTTP satisfactoria y contiene `status=ok`, `service=storage`,
el `node_id` esperado y una escuela válida. Los resultados distinguen respuesta
saludable, respuesta inválida, error HTTP, error del servidor, timeout y nodo
inaccesible.

Las comprobaciones HTTP terminan antes de abrir la transacción que actualiza el
catálogo. El monitor reutiliza `storage_nodes.health` (`up`, `down`, `unknown`)
y escribe `last_seen_at` solamente tras una respuesta válida. No fue necesaria
una migración.

## Umbrales configurables

```dotenv
HEALTH_CHECK_INTERVAL_SECONDS=5
HEALTH_CHECK_TIMEOUT_SECONDS=2
HEALTH_FAILURE_THRESHOLD=3
HEALTH_RECOVERY_THRESHOLD=2
```

Intervalo y timeout deben ser positivos; ambos umbrales deben ser enteros
mayores o iguales a uno. Los contadores son independientes por nodo y se
reinician al cambiar el resultado de éxito a fallo o viceversa.

Con los valores predeterminados, tres fallos consecutivos cambian `up` o
`unknown` a `down`; dos éxitos consecutivos cambian `down` o `unknown` a `up`.
Un éxito siempre actualiza `last_seen_at`, incluso antes de alcanzar el umbral
de recuperación. Los logs de fallos y transiciones incluyen nodo, host físico,
resultado, duración, contadores y estados anterior/nuevo.

Los contadores viven en memoria. Al reiniciar el proceso vuelven a cero, pero
el estado persistido no cambia: un nodo `down` necesita nuevamente dos éxitos
para recuperarse y un nodo `up` necesita nuevamente tres fallos para caer. Esta
decisión evita convertir todos los nodos en `up` durante un reinicio.

## Ejecución con Compose

Primero aplica las migraciones y el seed como se explica en
[`postgresql.md`](postgresql.md). Después inicia el monitor junto con los otros
perfiles:

```bash
docker compose -f infrastructure/docker/compose.yaml \
  --profile coordinator --profile storage --profile monitor \
  up --build -d

docker compose -f infrastructure/docker/compose.yaml logs -f health-monitor
```

El monitor puede detenerse o iniciarse sin afectar los demás servicios:

```bash
docker compose -f infrastructure/docker/compose.yaml stop health-monitor
docker compose -f infrastructure/docker/compose.yaml start health-monitor
```

Una cerradura consultiva de PostgreSQL impide que dos instancias del monitor
operen simultáneamente sobre el mismo catálogo. `SIGINT` y `SIGTERM` detienen el
bucle y liberan la conexión de forma controlada.

Para el entorno E2E aislado:

```bash
docker compose -f infrastructure/docker/compose.rf3-test.yaml \
  --profile monitor up --build -d --wait

docker compose -f infrastructure/docker/compose.rf3-test.yaml stop node-math-test
docker compose -f infrastructure/docker/compose.rf3-test.yaml \
  logs --timestamps health-monitor-test

docker compose -f infrastructure/docker/compose.rf3-test.yaml start node-math-test
```

Consulta el estado persistido sin modificarlo:

```bash
docker compose -f infrastructure/docker/compose.rf3-test.yaml \
  exec -T postgres-test psql -U yachayrepo_rf3_test -d yachayrepo_rf3_test \
  -c "SELECT id, health, last_seen_at FROM storage_nodes ORDER BY id;"
```

No uses `down -v`: los experimentos normales conservan los volúmenes.

## Resultados E2E observados

El 23 de noviembre de 2026 se ejecutó `compose.rf3-test.yaml` con intervalo de
un segundo, timeout de 0,5 segundos y umbrales 3/2:

- `node-math-test` se detuvo a las `21:16:14.442Z`; los fallos 1 y 2 quedaron
  en estado `up` y la transición a `down` se registró a las `21:16:17.461Z`:
  aproximadamente 3,02 segundos.
- El nodo se inició a las `21:16:33.811Z`; después del arranque del servicio y
  dos respuestas válidas, la transición a `up` ocurrió a las
  `21:16:36.899Z`: aproximadamente 3,09 segundos desde el comando de inicio.
- `node-phy` y `node-earth` cayeron de forma independiente mientras los otros
  cuatro permanecieron `up` y `GET /health` del coordinador respondió `ok`.
- Una descarga RF=3 con `node-math=down` produjo SHA-256
  `b1aac9d77dd5a572daad74c10d16a6afd5a033eb67fa2b1707437577529bcf03`,
  idéntico al de la carga. Sus tres réplicas continuaron `healthy` y la versión
  continuó `available`.
- Un reinicio conservó en PostgreSQL los tres nodos que estaban `down`. Una
  segunda instancia fue rechazada por la cerradura consultiva. Tras restaurar
  los contenedores, los seis nodos volvieron a `up`, el hash se conservó y no
  cambió ningún estado de réplica.

Los tiempos incluyen el intervalo hasta el siguiente ciclo y, durante la
recuperación, el tiempo que necesitó el contenedor para volver a responder. Son
mediciones de este entorno aislado, no garantías para una red Raspberry Pi.

## Relación con failover y réplicas

El failover excluye candidatos cuyo nodo esté `down`, pero conserva sus
comprobaciones HTTP directas para tolerar estados temporalmente desactualizados.
El monitor modifica exclusivamente el estado y última respuesta del nodo. Nunca
modifica `document_replicas.status` ni `document_versions.status`: un nodo
apagado puede seguir conteniendo archivos íntegros, y recuperar conectividad no
demuestra que una réplica `missing` o `corrupted` haya sido reparada.

`find_replication_deficits()` ofrece un diagnóstico interno y de solo lectura.
Para cada versión disponible distingue réplicas registradas, réplicas marcadas
`healthy` y réplicas accesibles en nodos y hosts activos que no estén `down`.
Solo devuelve versiones con menos copias accesibles que `target_replicas`. No
crea copias ni expone un endpoint administrativo.

## Raspberry Pi y limitaciones

El proceso no depende de nombres Docker: las seis URLs llegan mediante las
variables `NODE_*_URL`, mientras que la ubicación física se obtiene únicamente
de `physical_host_id` en PostgreSQL. En Raspberry Pi solo deben cambiar las URLs
y la ubicación del servicio que ejecuta el monitor.

La reparación automática se ejecuta en el proceso independiente descrito en
[`replica-repair.md`](replica-repair.md); el monitor nunca transfiere archivos.
La reconciliación de subidas incompletas y su auditoría persistente se describen
en [`upload-reconciliation.md`](upload-reconciliation.md). Todavía no existen
historial persistente de cada comprobación del monitor ni dashboard. Los contadores no
sobreviven al reinicio y PostgreSQL continúa siendo un punto único de fallo.
