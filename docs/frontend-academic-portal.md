# Portal académico y visor PDF

La Fase 7.2 extiende el layout y la sesión de la [Fase 7.1](frontend-foundation.md). Todas las rutas académicas pasan por el guard autenticado y el control de contraseña temporal.

| Ruta | Vista |
|---|---|
| `/` | Bienvenida, búsqueda y cuatro publicaciones recientes |
| `/schools` | Catálogo real de escuelas |
| `/schools/$schoolId` | Materias y publicaciones de una escuela |
| `/schools/$schoolId/subjects/$subjectId` | Materia y documentos asociados |
| `/documents` | Explorador con filtros y paginación |
| `/documents/$documentId` | Metadatos, versión seleccionada, visor y descarga |

## Consulta

`src/api/academic.ts` reutiliza Axios y define consultas con claves por parámetros y señal de cancelación. Escuelas, materias y tipos provienen de `/catalogs/schools`, `/subjects` y `/catalogs/document-types`. El explorador persiste `school`, `subject`, `type`, `q` y `page` en la URL. Búsqueda por título con 350 ms de debounce. Cambiar escuela limpia la materia y cambiar filtros reinicia la página.

`GET /documents` conserva la respuesta de lista y acepta `q`, `offset`, `limit` y `document_id` además de los filtros existentes. La consulta selecciona versiones publicadas y omite borrados lógicos en PostgreSQL. Orden determinista por fecha de creación del documento descendente y UUID. El cliente solicita 13 resultados para mostrar hasta 12 e identificar la siguiente página. La API carece de total. El detalle por UUID reutiliza esa misma consulta.

`GET /documents/{id}/versions` proporciona el historial. La interfaz usa `current_version_id` para descubrir la versión actual y muestra solo las disponibles. `?version=N` identifica una versión histórica y los enlaces de regreso conservan los filtros.

## Visor y descarga

PDF.js se carga de forma diferida al visualizar el primer PDF. El worker se empaqueta localmente mediante Vite. La vista renderiza una página en canvas y limita la resolución del lienzo. Incluye navegación, salto a página, zoom entre 50 y 250 %, ajuste al ancho y pantalla completa cuando el navegador lo admite. La información cae debajo del visor en pantallas estrechas y el canvas ampliado desplaza únicamente su contenedor.

Los bytes provienen de `GET /documents/{id}/versions/{number}/download` con Bearer y timeout de dos minutos. El coordinador conserva selección de réplica, failover e integridad SHA-256. Axios recibe el archivo completo como Blob antes de la descarga local. En vista previa se convierte a Uint8Array para PDF.js. Los bytes se mantienen exclusivamente en el componente activo. Al cambiar de versión o salir de la pantalla se abortan las solicitudes y se destruyen las tareas PDF. La URL temporal para guardar el archivo se revoca después de iniciar la descarga y durante el desmontaje si sigue vigente.

El navegador lee `Content-Disposition` a través de CORS, prioriza `filename*` y aplica sanitización y fallback al nombre registrado. Para archivos de otros formatos se ofrece descarga. PDFs dañados o cifrados reciben un mensaje visible. Fallos de réplica o red muestran un estado recuperable. La validación del JWT sigue a cargo del backend.

## Ejecución y pruebas

Inicia el coordinador con el origen `http://localhost:5173` permitido y ejecuta `npm ci` y `npm run dev` desde `frontend/`. Usa las instrucciones de credenciales y Compose de la Fase 7.1. Para el E2E académico inicia también los seis nodos de `compose.rf3-test.yaml` con el proyecto aislado `yachayrepo-academic-validation`. Aplica Alembic, seed y bootstrap en ese catálogo de pruebas.

```bash
npm run lint
npm run typecheck
npm test
npm run build
VITE_API_BASE_URL=http://127.0.0.1:18000 E2E_NODE_FAILURES=1 npm run e2e -- --workers=1
```

Exporta antes las variables `E2E_ADMIN_EMAIL` y `E2E_ADMIN_PASSWORD` de prueba. El escenario académico crea cuentas y documentos temporales mediante la API, compara SHA-256 y restaura los nodos de prueba al finalizar. Omite `E2E_NODE_FAILURES` para ejecutar el escenario sin detener nodos.

## Límites y siguiente fase

La búsqueda es por título y las páginas reflejan el catálogo al momento de cada consulta. El visor carga el archivo completo y presenta una representación visual en canvas. La Fase 7.3 podrá reutilizar catálogos, permisos y navegación para las operaciones del profesor.
