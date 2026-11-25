# Reconciliación automática de subidas fallidas

YachayRepo ejecuta `upload-reconciler` como un proceso independiente del
coordinador, monitor y reparador. Recupera versiones `pending` o `failed` que
conservaron al menos una reserva de réplica; no crea una nueva versión, no
cambia `target_replicas` y no interviene en versiones ya `available`.

## Arquitectura y persistencia

PostgreSQL es la fuente de verdad. La migración `20261123_05` agrega:

- `reconciliation_jobs`: un trabajo único por `version_id`, su estado, número
  de intentos, próximo reintento, último error y fechas de ciclo de vida;
- `reconciliation_attempts`: historial inmutable de cada ejecución, snapshot de
  las réplicas, destino elegido, resultado y detalle.

Cada ciclo descubre operaciones incompletas, limita la concurrencia y toma una
cerradura consultiva por versión. Esa cerradura usa la misma clave que el
reparador, por lo que ambos procesos no transfieren la misma versión a la vez.
Una cerradura global impide ejecutar dos reconciliadores completos.

Los estados del trabajo son `pending`, `in_progress`, `retry_wait`,
`completed`, `failed` y `cancelled`. Son independientes del estado de la
versión y del estado de cada réplica. Un reinicio con un intento
`in_progress` lo cierra como `retry` y continúa sobre el mismo trabajo.

## Selección, verificación e idempotencia

La selección incluye únicamente versiones incompletas que ya poseen reservas.
Antes de transferir se consulta el catálogo y se verifica físicamente cada
copia accesible. Tamaño y SHA-256 deben coincidir con `document_versions`;
réplicas `missing` o `corrupted` nunca se usan como fuente. Una caída de red,
timeout o HTTP 5xx se trata como indisponibilidad temporal.

El destino debe estar configurado y activo, pertenecer a un host activo y no
compartir `physical_host_id` con otra copia de la versión. Se reutiliza primero
una reserva existente; después se considera un nodo nuevo. La prioridad del
nodo de la escuela se conserva cuando es compatible con la anticolocación.

La transferencia usa `PUT /internal/repair/{storage_id}` con
`X-Repair-Token`. El endpoint escribe y publica atómicamente y es idempotente:
si los bytes ya llegaron antes de una interrupción, la siguiente verificación
los confirma sin repetir el `PUT`. La versión 1 conserva el UUID del documento
como `storage_id`; las posteriores usan su propio UUID.

## Reintentos y publicación atómica

```dotenv
RECONCILIATION_ENABLED=true
RECONCILIATION_INTERVAL_SECONDS=15
RECONCILIATION_RETRY_BACKOFF_SECONDS=30
RECONCILIATION_MAX_ATTEMPTS=10
RECONCILIATION_MAX_CONCURRENT_JOBS=2
RECONCILIATION_TRANSFER_TIMEOUT_SECONDS=120
```

Intervalo y timeout deben ser positivos, intentos y concurrencia al menos uno,
y backoff no negativo. Los fallos de conexión, transferencia y destino aplican
backoff persistente. El límite detiene transferencias fallidas repetidas. La
ausencia temporal de todas las fuentes es una espera de sondeo, no evidencia de
pérdida: permanece en `retry_wait` con backoff incluso si supera ese presupuesto
y se recupera automáticamente cuando vuelve una copia. Solo la ausencia
confirmada de toda fuente íntegra se marca `failed`.

La publicación ocurre en una única transacción después de verificar al menos
`target_replicas` copias sanas en hosts distintos. Entonces la versión pasa a
`available` y el trabajo a `completed`. `current_version_id` solo avanza si no
existe una versión disponible más nueva; reconciliar una versión histórica
nunca reemplaza la actual. Un documento eliminado lógicamente cancela el
trabajo sin publicar.

## Integración con monitor, reparador y failover

El monitor solo actualiza salud; el reconciliador interpreta esos estados pero
también tolera que una petición falle antes de que el monitor confirme la
caída. El reparador sigue dedicado a déficits de versiones `available`. Una vez
publicada la subida recuperada, su mantenimiento normal corresponde al
reparador y sus lecturas usan el failover existente.

## Docker Compose

Aplica migraciones y activa el perfil `reconciliation` junto con los demás:

```bash
python -m alembic -c backend/alembic.ini upgrade head

docker compose -f infrastructure/docker/compose.yaml \
  --profile coordinator --profile storage --profile monitor \
  --profile repair --profile reconciliation up --build -d

docker compose -f infrastructure/docker/compose.yaml logs -f upload-reconciler
```

Se puede detener y reiniciar únicamente este proceso sin afectar las cargas ni
descargas. No uses `down -v`: las reservas y archivos de los nodos son parte de
la evidencia necesaria para recuperar una operación.

## Pruebas y resultados E2E

La suite cubre RF=1/RF=3, recuperación con una o dos copias, corrupción,
timeouts, HTTP 500, backoff, límite de transferencias, reinicios, idempotencia,
versiones históricas, borrado lógico, concurrencia, auditoría y ausencia
temporal de todas las fuentes.

El 24 de noviembre de 2026 se validó con `compose.rf3-test.yaml` aislado:

- una subida RF=3 con dos copias pasó de HTTP 503 a `available` con tres SHA-256
  idénticos y auditoría `completed`;
- bytes transferidos antes de confirmar PostgreSQL se detectaron con `GET`, sin
  un segundo `PUT`;
- una versión 2 recuperada no sustituyó a la versión 3 actual;
- RF=1 publicó exactamente una réplica sana;
- con todas las fuentes apagadas el trabajo permaneció en `retry_wait`; al
  restaurarlas terminó con tres réplicas sanas;
- una segunda instancia fue rechazada y monitor, reparador y reconciliador
  permanecieron activos simultáneamente.

## Limitaciones y preparación para frontend

PostgreSQL continúa como punto único de fallo. No hay transferencia directa
nodo a nodo, cola externa, dashboard ni autenticación de usuarios; el token
interno compartido es una protección mínima de servicio. Las fuentes ausentes
se sondean según el backoff mientras no exista evidencia concluyente de pérdida.

Las tablas de auditoría ya exponen el estado, intentos, error, destino y marcas
de tiempo necesarios para un futuro panel. Esta fase no agrega endpoints
administrativos ni frontend: esa API debe diseñarse después con autorización y
sin permitir que el cliente altere directamente los estados del trabajo.
