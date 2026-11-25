# Reparación automática de réplicas

YachayRepo ejecuta `replica-repair` como proceso independiente del coordinador
y del monitor. Solo reconcilia versiones `available`; las subidas cuya versión
quedó `pending` o `failed` corresponden al proceso descrito en
[`upload-reconciliation.md`](upload-reconciliation.md). Ninguno cambia
`target_replicas`, y ambos protegen versiones históricas y documentos
eliminados lógicamente.

## Arquitectura y configuración

El reparador consulta PostgreSQL, limita los trabajos concurrentes y adquiere
una cerradura consultiva por versión antes de reservar, transferir o retirar
una copia. Una segunda instancia completa es rechazada por otra cerradura
consultiva global.

```dotenv
REPAIR_ENABLED=true
REPAIR_INTERVAL_SECONDS=10
REPAIR_DOWN_GRACE_SECONDS=60
REPAIR_MAX_CONCURRENT_JOBS=2
REPAIR_TRANSFER_TIMEOUT_SECONDS=120
REPAIR_RETRY_BACKOFF_SECONDS=30
REPAIR_EXCESS_CLEANUP_ENABLED=true
REPAIR_INTERNAL_TOKEN=CHANGE_ME_TO_A_LONG_RANDOM_VALUE
```

Intervalo y timeout deben ser positivos; gracia y backoff admiten cero, pero no
valores negativos; la concurrencia mínima es uno y los booleanos solo aceptan
`true` o `false`. El token debe ser compartido exclusivamente por reparador y
nodos de almacenamiento y debe reemplazarse fuera del desarrollo local.

La migración `20261123_04` agrega:

- `storage_nodes.health_changed_at`: instante persistente del último cambio de
  salud confirmado por el monitor. Al migrar, `now()` establece un baseline
  conservador; cada transición posterior reemplaza el valor.
- `document_replicas.repair_after`: instante persistente del próximo reintento
  de una reserva fallida.

`last_seen_at` conserva su semántica de última respuesta exitosa y nunca se usa
como fecha de caída.

## Planificación y prioridad

El orden de trabajo es:

1. reservas `missing` o `corrupted`, sin esperar la gracia;
2. reservas `pending` de reparaciones interrumpidas;
3. recuperación de una copia íntegra en el nodo cuyo `home_school_id` coincide
   con la escuela propietaria;
4. déficit causado por nodos `down`, únicamente tras la gracia;
5. limpieza de copias excedentes.

La relación escuela–nodo se consulta en PostgreSQL. No existen mapas de IP,
puerto o nombre físico en el reparador. Un destino debe estar configurado,
activo, accesible, en un host activo y no compartir `physical_host_id` con otra
réplica registrada de la versión. Las restricciones
`uq_replica_version_host` y `uq_replica_version_node` continúan siendo la última
línea de defensa.

Una caída conserva la fila y el archivo antiguo. Tras la gracia se reserva una
fila `pending` en el cuarto host disponible y, cuando la transferencia queda
verificada, pasa a `healthy`. Por eso pueden existir temporalmente cuatro
registros en cuatro hosts distintos sin modificar las restricciones.

## Fuente, transferencia y reintentos

Las fuentes son réplicas `healthy` en nodos y hosts accesibles. Se prueban en
orden determinista y cada una se descarga por bloques a un temporal. Tamaño y
SHA-256 deben coincidir con `document_versions`; una fuente ausente o corrupta
se clasifica y se intenta la siguiente. Sin fuente válida no se fabrica
contenido.

La transferencia al destino usa el endpoint interno autenticado
`PUT /internal/repair/{storage_id}`. El nodo escribe un temporal, sincroniza,
valida tamaño y SHA-256 y publica con reemplazo atómico. La operación es
idempotente: si ya existe exactamente la copia solicitada, confirma el mismo
resultado. PostgreSQL se actualiza después del HTTP, sin mantener una
transacción abierta durante la red.

Una reserva se crea antes de transferir. Si el proceso se interrumpe, la fila
`pending` sobrevive y el ciclo siguiente la reanuda. Si los bytes fueron
publicados pero faltó confirmar el catálogo, el reintento idempotente los
reconoce. Los errores establecen `repair_after` según el backoff.

## Limpieza segura

Cuando un nodo se recupera pueden existir cuatro copias accesibles. El conjunto
a conservar prioriza primero el nodo de origen y después nodos secundarios en
orden determinista, siempre en hosts distintos. Antes de retirar se comprueba
que existen al menos `target_replicas` alternativas íntegras y accesibles.

La eliminación usa `DELETE /internal/repair/{storage_id}` con token y SHA-256
esperado; nunca borra directamente desde el reparador. El nodo verifica la
copia exacta, la elimina de manera idempotente y el catálogo se vuelve a
comprobar antes de borrar su fila. Si el HTTP falla, la fila se conserva. Las
descargas abren el archivo antes de iniciar el streaming, de modo que una
lectura ya iniciada conserva su descriptor aunque después se retire la ruta.

## Docker Compose

Aplica primero las migraciones y después activa los cuatro perfiles:

```bash
docker compose -f infrastructure/docker/compose.yaml \
  --profile coordinator --profile storage --profile monitor --profile repair \
  up --build -d

docker compose -f infrastructure/docker/compose.yaml logs -f replica-repair
```

El proceso puede detenerse y reiniciarse sin detener al coordinador:

```bash
docker compose -f infrastructure/docker/compose.yaml stop replica-repair
docker compose -f infrastructure/docker/compose.yaml start replica-repair
```

En Raspberry Pi solo cambian las variables `NODE_*_URL`. La selección física
continúa usando el catálogo central. No se deben borrar volúmenes con `down -v`.

## Resultados E2E observados

El 23 de noviembre de 2026 se usó `compose.rf3-test.yaml` con ciclo de un
segundo y gracia de cuatro segundos. El documento de prueba tuvo SHA-256
`818130e1b4acaa59e9193fdb9cea773fe8d12410774d81386fb0087d7f65712a`.
La suite completa, incluido PostgreSQL real, terminó con `135 passed`.

- Caída secundaria: `node-agro` pasó a `down` a las `21:57:10.347Z`; la copia
  en `node-chem` quedó verificada a las `21:57:15.474Z` (5,13 s). Se conservaron
  cuatro filas en `raspberry-1/2/3/4` y tres copias accesibles.
- Recuperación secundaria: `node-agro` pasó a `up` a las `21:57:42.034Z`; la
  copia temporal de `node-chem` fue retirada a las `21:57:42.826Z` (0,79 s).
- Caída del origen: `node-math` pasó a `down` a las `21:58:00.476Z`; el
  reemplazo en `node-chem` terminó a las `21:58:05.010Z` (4,53 s).
- Recuperación del origen: `node-math` volvió a `up` a las `21:58:27.039Z`; se
  conservó y el reemplazo secundario se retiró a las `21:58:28.095Z` (1,06 s).
- Una ausencia confirmada por failover se restauró en `node-math` en
  aproximadamente 0,47 s; una corrupción confirmada, en 0,39 s. Ambas
  descargas de failover y las copias restauradas conservaron el SHA-256.
- Una reserva `pending` simulada sobrevivió al reinicio: desde el log de inicio
  del proceso hasta la confirmación transcurrieron 0,24 s, quedaron exactamente
  tres filas y no hubo duplicados.
- Una segunda instancia fue rechazada por la cerradura global.

Los tiempos son mediciones del entorno Docker local, no garantías de latencia
para una red de Raspberry Pi.

## Limitaciones

PostgreSQL sigue siendo un punto único de fallo. No existe transferencia directa
nodo a nodo, reparación distribuida sin catálogo, limpieza de residuos ajenos
al catálogo ni dashboard. La reconciliación general de subidas fallidas se
ejecuta en un proceso separado. El token compartido es una protección interna
mínima hasta que exista autenticación de servicios.
