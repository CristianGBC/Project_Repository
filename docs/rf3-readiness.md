# Revisión arquitectónica previa a RF = 3

> Esta revisión corresponde al estado anterior a la Fase 1. La implementación
> actual se documenta en [`rf3-replication.md`](rf3-replication.md).

Este documento analiza la preparación existente; no implementa replicación,
failover, monitoreo ni reparación automática.

## Capacidades reutilizables

- `document_versions.target_replicas` permite conservar RF = 1 y solicitar RF = 3
  por versión.
- `document_replicas` admite varias filas por `version_id` y mantiene estado,
  intentos, error y fechas de almacenamiento/verificación independientes.
- `storage_nodes.physical_host_id` identifica la ubicación física de cada nodo.
- `uq_replica_version_node` impide duplicar una versión en el mismo nodo.
- `uq_replica_version_host` impide colocar dos réplicas de una versión en nodos
  del mismo host físico.
- La clave foránea compuesta de la réplica comprueba que `node_id` realmente
  pertenece al `physical_host_id` registrado.
- Tamaño y SHA-256 pertenecen a `document_versions`; todas las réplicas de una
  versión comparten por definición esos valores.
- Las versiones son inmutables y cada versión tiene sus propias réplicas. El
  borrado lógico solo modifica `documents` y conserva ambas colecciones.

No hacen falta tablas nuevas para representar RF = 3. Tampoco se requiere una
migración antes de implementar el flujo básico. Las restricciones actuales ya
expresan la anticolocación por host físico.

## Componentes que deberán ampliarse

1. Reemplazar `TARGET_REPLICAS = 1` por una configuración validada por entorno o
   política, conservando `1` y `3` como valores soportados.
2. Cambiar `_select_storage_node` por un selector de varios nodos activos,
   distintos por `physical_host_id`, con preferencia opcional por el nodo local
   de la escuela.
3. Crear en una sola transacción las tres filas `pending` de
   `document_replicas` antes de transferir bytes.
4. Extender `_store_on_node`, `_record_failed_transfer` y
   `_confirm_stored_replica` para coordinar resultados por réplica y decidir
   cuándo una versión cumple su política de disponibilidad.
5. Hacer que la descarga pruebe réplicas `healthy` alternativas y marque de
   forma independiente las copias ausentes o corruptas.
6. Añadir un reconciliador durable para filas `pending`, `failed`, `missing` o
   `corrupted`, separado del camino síncrono de la API.
7. Actualizar respuestas y observabilidad para mostrar la salud agregada sin
   perder el detalle individual.

## Decisiones pendientes

- Definir si una versión se publica al confirmar las tres réplicas o con un
  cuórum, y cómo se representa una versión parcialmente replicada. El estado
  actual `pending/available/failed` puede resultar insuficiente para distinguir
  `available` de `under_replicated`; sería preferible derivar ese estado de las
  réplicas o añadirlo mediante una migración futura.
- Definir timeout global, paralelismo, reintentos y compensación. Las
  transferencias deberían ejecutarse concurrentemente, pero cada resultado
  debe confirmarse en una transacción corta.
- Definir una reserva consistente de capacidad cuando varias cargas eligen
  nodos simultáneamente. La selección actual no considera espacio libre ni
  operaciones concurrentes en curso.
- Decidir si `storage_nodes.health` seguirá siendo una señal administrativa o
  será actualizado por monitoreo. No debe confundirse con una comprobación de
  salud instantánea.
- Definir qué réplica se lee primero y cómo evitar que solicitudes simultáneas
  dupliquen una reparación.

## Riesgos de concurrencia y consistencia

- Dos cargas concurrentes pueden seleccionar los mismos hosts si el selector no
  incorpora capacidad o reservas, aunque esto no viola la anticolocación dentro
  de una sola versión.
- Puede almacenarse un archivo y fallar la confirmación en PostgreSQL. Las filas
  `pending` deben conservarse como registro de reconciliación, igual que en RF = 1.
- Una respuesta tardía no debe degradar una réplica ya confirmada ni cambiar la
  versión actual por una versión anterior.
- El éxito parcial deja copias físicas durables; no deben borrarse de forma
  automática hasta definir la política de reintento y reconciliación.
- PostgreSQL continúa siendo un punto único de fallo aunque existan tres copias
  de los bytes.
- RF = 3 requiere al menos tres hosts físicos activos. Los seis nodos actuales
  se distribuyen entre cuatro hosts, por lo que los datos iniciales lo permiten.

## Compatibilidad con RF = 1

La política debe residir en configuración o en `target_replicas`, no quedar
codificada únicamente como tres. El flujo deberá crear exactamente el número
solicitado de réplicas y calcular disponibilidad con esa meta. Así pueden
compararse RF = 1 y RF = 3 usando el mismo modelo y la misma API.
