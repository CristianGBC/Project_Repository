# Monitorización distribuida · Fase 7.5

Abre **Monitorización** en el sidebar o visita `/admin/operations`. La vista consume el cliente HTTP autenticado del coordinador. Agrupa nodos por `physical_host_id` con datos de PostgreSQL.

| Vista bajo /admin/operations | API bajo /operations |
|---|---|
| Resumen | overview, nodes, hosts |
| topology | hosts, nodes |
| nodes y nodes/$nodeId | nodes, hosts, replicas filtradas por nodo |
| replicas | replicas con filtros y paginación |
| documents/$documentId | documents/{document_id}, replicas de la versión seleccionada |
| deficits | deficits y registros de reparación consultables en réplicas |
| reconciliation y reconciliation/$jobId | reconciliation/jobs y reconciliation/jobs/{job_id}/attempts |

`overview` y `documents/{document_id}` son nuevos. Réplicas y trabajos admiten filtros y offset. Hosts expone dirección y nodos expone puerto a superadministración. El API entrega estados y fechas de error junto a un marcador cuando existe un error guardado. El texto interno permanece reservado.

Superadministración accede a todo. Administración escolar recibe la información básica histórica de `nodes`. Docentes y estudiantes carecen de acceso. Topología muestra la disponibilidad observada de nodos, sin inferir el estado físico de una máquina o de PostgreSQL. El navegador consulta exclusivamente el coordinador.

Los contadores de documentos, versiones, réplicas y trabajos salen de agregaciones SQL. Incluyen registros de documentos eliminados cuando siguen en el catálogo. El déficit y exceso globales corresponden a versiones disponibles. El exceso global mide registros por encima del RF. El inspector distingue salud, accesibilidad estimada, déficit, exceso y antiafinidad por versión. El origen preferido se deriva de la escuela. Un nodo UNKNOWN habilitado puede contar como accesible según la regla operativa vigente. La salud reciente de un nodo no verifica sus archivos.

El reconciliador crea los trabajos cuando descubre versiones pendientes. Una
subida incompleta puede estar a la espera de recuperación antes de que aparezca
un trabajo en el panel.

TanStack Query actualiza cada cinco segundos mientras la vista está activa. La actualización manual refuerza el sondeo. Los errores conservan los últimos datos válidos mientras se mantenga la consulta. La fecha indicada corresponde a los datos recibidos por el navegador. Los tiempos de verificación de réplicas y de observación de nodos se muestran por separado. CPU, RAM, disco, temperatura, series históricas, progreso actual del reparador y salud física quedan sin verificar.

## Ejecución aislada

Desde la raíz del repositorio, con los puertos de pruebas libres:

```bash
dc75() { docker compose -p yachayrepo-75-browser -f infrastructure/docker/compose.rf3-test.yaml --profile reconciliation --profile repair --profile monitor "$@"; }
dc75 up -d --build --wait postgres-test coordinator-test node-bio-test node-phy-test node-math-test node-chem-test node-earth-test node-agro-test upload-reconciler-test
dc75 exec coordinator-test python -m alembic -c backend/alembic.ini upgrade head
dc75 exec coordinator-test python -m backend.app.seed
dc75 exec -e BOOTSTRAP_ADMIN_EMAIL=ops@example.org -e BOOTSTRAP_ADMIN_FIRST_NAME=Operaciones -e BOOTSTRAP_ADMIN_LAST_NAME=Pruebas coordinator-test python -m backend.auth.bootstrap_admin
```

Desde `frontend`, abre la app con `VITE_API_BASE_URL=http://127.0.0.1:18000 npm run dev` y entra con esa cuenta de ensayo. Para Playwright, detén el Vite manual, instala Chromium con `npx playwright install chromium` y exporta el correo y la contraseña de ensayo en `E2E_ADMIN_EMAIL` y `E2E_ADMIN_PASSWORD`. Ejecuta:

```bash
VITE_API_BASE_URL=http://127.0.0.1:18000 E2E_ISOLATED_PROJECT=yachayrepo-75-browser \
  E2E_NODE_FAILURES=1 E2E_PROFESSOR_FAILURES=1 npm run e2e -- --workers=1
```

Al finalizar, vuelve a la raíz y ejecuta `dc75 stop`. [Consistencia y casos de prueba](distributed-consistency-audit.md).

En una ventana posterior de despliegue, actualiza el checkout autorizado del coordinador y ejecuta `dc build coordinator frontend` seguido de `dc up -d --no-build coordinator frontend`. Verifica el resumen con una cuenta autorizada y revisa los estados antes de cerrar la ventana. La fase no añade migraciones, dependencias ni variables. [Despliegue físico y rollback](physical-deployment.md).
