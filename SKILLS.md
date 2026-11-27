# Guía operativa de YachayRepo

## Development Environment

Para trabajar en la SPA, usa npm desde `frontend/` y sigue la [guía del frontend](docs/frontend-foundation.md). Valida con `npm run lint`, `npm run typecheck`, `npm test` y `npm run build`.

Ejecuta los comandos desde la raíz del repositorio. Usa Python 3.14 para reproducir las imágenes, Docker con Compose v2 y acceso a PostgreSQL. Crea y activa una virtualenv si hace falta.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
```

Consulta `infrastructure/docker/.env.example` y `backend/app/config.py`. Usa un archivo local ignorado y pásalo explícitamente con `--env-file .env` si lo guardas en la raíz. Configura `DATABASE_URL` con Psycopg 3, `NODE_*_URL` según topología, `REPLICATION_FACTOR`, `AUTH_JWT_SECRET`, los tokens internos compartidos según cada par de servicios y los ajustes de monitor, reparación y reconciliación. Usa credenciales propias para despliegues compartidos. Para pytest usa exclusivamente `TEST_DATABASE_URL` aislada.

Para desarrollo del coordinador con el entorno exportado, ejecuta `python -m uvicorn backend.app.main:app --port 8000` y detén con Ctrl+C. Para el conjunto distribuido usa Compose. Aplica el esquema y seed antes del primer arranque con catálogo. Consulta el bootstrap en [Autenticación y RBAC](docs/authentication-rbac.md).

## Coding Standards

- Sigue los nombres Python en snake_case y los contratos Pydantic existentes. Mantén HTTP y dependencias de acceso en rutas, lógica de dominio en servicios, configuración en sus módulos y persistencia en ORM o almacenamiento según corresponda.
- Mantén los códigos de error de rutas vecinas. Valida UUID, tamaño, checksum, entidades activas, pertenencia escolar y permisos donde corresponda. Usa constraints e integridad transaccional para invariantes concurrentes.
- Sigue los loggers y campos de diagnóstico del componente. Registra identificadores y estados útiles. Excluye credenciales, JWT y contenido de archivos. Mantén separados los permisos de usuarios y las credenciales de servicio.

## Development Workflows

| Tarea | Procedimiento y aceptación |
|---|---|
| Funcionalidad nueva | Empieza por CODEX y busca el flujo equivalente. Traza ruta, servicio, modelo y contrato. Confirma alcance y permisos. Aceptación con prueba del caso nuevo y regresión en el flujo existente. |
| Corrección | Identifica la ruta o proceso, inspecciona estado y logs y reproduce con mocks o catálogo aislado. Corrige la causa. Comprueba el caso fallido y su ruta normal. |
| Endpoint | Revisa `backend/app/api/`, `schemas/` y `auth.py`. Reutiliza dependencias y errores. Comprueba éxito, entradas inválidas, permisos y OpenAPI. |
| Servicio | Sigue el patrón de `backend/app/services/`. Define límites transaccionales y comportamiento ante fallo y reintento. Valida con HTTPX simulado y PostgreSQL aislado cuando intervenga el catálogo. |
| Modelo o migración | Revisa `models/catalog.py`, migraciones y seed. Genera revisión incremental, inspecciona SQL y valida upgrade de una base previa y limpia en un entorno aislado. Aceptación con esquema consistente y datos previos legibles. |
| Componente distribuido | Revisa `backend/storage/`, los entrypoints de mantenimiento y Compose. Usa identidad y credenciales internas existentes. Prueba caída, reinicio, recuperación y SHA-256 en laboratorio. |
| Configuración | Cambia ejemplo, lector y Compose según corresponda. Usa `docker compose config --quiet` y verifica el perfil afectado y compatibilidad de variables. |
| Refactor | Acota el módulo y preserva contratos, estados y permisos. Ejecuta las pruebas que cubren a sus consumidores. |
| Documentación | Contrasta comandos, revisiones y rutas con el código. Actualiza la guía específica y la memoria solo si cambia contexto duradero. Valida enlaces y diff. |

Con el entorno de destino cargado, confirma nombre de base mediante `SELECT current_database();`. Para una base con datos valida un respaldo antes del upgrade. Desde la raíz:

```bash
python -m alembic -c backend/alembic.ini heads
python -m alembic -c backend/alembic.ini current
python -m alembic -c backend/alembic.ini upgrade head
python -m alembic -c backend/alembic.ini current
python -m backend.app.seed
```

## Testing and Validation

Para pruebas de alcance administrativo y credenciales temporales, sigue [la guía de la Fase 7.4](docs/frontend-administration.md) y usa cuentas del entorno aislado.

Para validar el portal docente con reconciliación, usa el proyecto aislado descrito en [la guía de la Fase 7.3](docs/frontend-professor-portal.md).

Para el portal, sigue la [guía académica](docs/frontend-academic-portal.md). Ejecuta las caídas de nodos exclusivamente con el proyecto Compose aislado.

```bash
env -u DATABASE_URL -u TEST_DATABASE_URL python -m pytest backend/tests -q
docker compose -f infrastructure/docker/compose.test.yaml up -d --wait
TEST_DATABASE_URL="$TEST_DATABASE_URL" python -m pytest backend/tests -q
git diff --check
```

Antes del segundo pytest exporta la URL de la instancia desechable. El sufijo `_test` es requerido por fixtures y algunas pruebas vacían tablas. El primer comando ejecuta pruebas de API y disco local y omite integraciones PostgreSQL. Para diagnosticar, usa el archivo afectado y `-x -vv`. Distingue errores de importación, fixtures omitidos, errores de esquema y regresiones funcionales.

Los escenarios E2E documentados usan `infrastructure/docker/compose.rf3-test.yaml`. Sigue las guías de RBAC, failover, reparación y reconciliación desde CODEX. Para validar fallos, usa datos de laboratorio, detén un nodo, compara SHA-256 y revisa estados y distribución de réplicas antes y después de recuperarlo.

## Docker and Infrastructure

Aplica las validaciones aisladas y el orden de actualización de
[integración final](docs/frontend-final-integration.md) al cambiar el catálogo.

Para validar la [monitorización distribuida](docs/frontend-distributed-dashboard.md)
usa un proyecto Compose y una base PostgreSQL aislados.

Usa [la guía de despliegue físico](docs/physical-deployment.md) para 8.3.
Valida el Compose del dispositivo y revisa `physical_catalog preview` antes
de actualizar destinos. Confirma los archivos de las réplicas antes de cambiar
rutas con datos y preserva los volúmenes del entorno anterior.

```bash
docker compose --env-file .env -f infrastructure/docker/compose.yaml \
  --profile coordinator --profile storage --profile monitor --profile repair \
  --profile reconciliation up --build -d
docker compose --env-file .env -f infrastructure/docker/compose.yaml ps
docker compose --env-file .env -f infrastructure/docker/compose.yaml logs --tail=100 coordinator
docker compose --env-file .env -f infrastructure/docker/compose.yaml stop
```

Selecciona perfiles según el host. Salud del coordinador en `/health`, nodos en `/nodes` y Swagger en `/docs`. Consulta logs del nodo o worker señalado por el diagnóstico. Usa `start SERVICIO` para recuperar un contenedor detenido, o `up -d --build SERVICIO` para aplicar cambios. Preserva los volúmenes y evita `down -v` durante operación ordinaria.

## Git Workflow

Verifica `git status -sb`, rama e historial antes de editar. Conserva modificaciones existentes. El historial usa ramas `feature/*` y `fix/*` y mensajes Conventional Commits como `feat:` y `fix:`. Usa la base indicada por el ticket. Solicita autorización explícita para cambios de rama, commits, pushes y operaciones destructivas.

## Debugging Guide

- **503 del catálogo.** Comprueba entorno efectivo sin imprimir secretos, host, puerto y revisión Alembic. Abre `database.py` y revisa logs del coordinador.
- **401 o 403.** Revisa expiración, usuario activo, membresía, escuela y matriz de permisos. Para errores entre servicios verifica el token interno esperado en cada extremo.
- **Subida parcial.** Consulta versiones, réplicas y trabajos vía catálogo u operaciones. Revisa destino, capacidad y logs de `upload-reconciler`.
- **Descarga fallida o réplica degradada.** Revisa `/nodes`, logs del monitor, `replica-repair`, ubicaciones y SHA-256. Confirma dominios físicos y disponibilidad de una fuente íntegra.
- **Respuesta inesperada.** Comprueba el commit e imagen ejecutados, perfiles activos y OpenAPI antes de atribuirlo al checkout.

## Definition of Done

Comprueba el comportamiento desde su entrada habitual, los casos de error pertinentes, permisos y compatibilidad. Ejecuta las pruebas aplicables y revisa el diff. Documenta límites y validaciones omitidas. Actualiza CODEX si cambia arquitectura o estado de una fase, y esta guía si aparece un procedimiento reutilizable. Corrige información obsoleta sin acumular un historial de tareas.
