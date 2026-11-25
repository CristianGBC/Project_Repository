# Portal del profesor — Fase 7.3

## Rutas y alcance

El layout incorpora navegación docente para las cuentas con una membresía activa de profesor. Los guardas consultan el perfil y mantienen el cambio obligatorio de contraseña. El coordinador comprueba la propiedad y las membresías en las escrituras.

- `/professor/documents`, `/professor/documents/deleted` y `/professor/uploads` muestran documentos propios, eliminados y seguimiento de subidas.
- `/professor/documents/new` permite publicar.
- `/professor/documents/$documentId`, `/edit` y `/new-version` bajo ese documento muestran el historial, editan metadatos y crean versiones.

El listado propio reutiliza `GET /documents/my/uploads` con filtros opcionales y paginación SQL. El lado académico conserva sus consultas existentes. Las mutaciones y la publicación observada tras reconciliación invalidan sus resultados en TanStack Query.

## Transferencia y estados

La primera subida usa `POST /documents` con `file`, `school_id`, metadatos y campos multipart repetidos `subject_ids`. Las opciones de escuela cruzan el catálogo activo con las membresías docentes. Cambiar escuela limpia materias. El tipo puede exigir una materia. El coordinador publica su límite en `X-Max-Upload-Bytes`. Las versiones nuevas usan `POST /documents/{id}/versions` con el archivo.

Axios mide los bytes del multipart cuando el navegador proporciona el total. El 100 % indica transferencia completa y el formulario sigue mostrando procesamiento hasta recibir la confirmación. Una petición activa bloquea envíos simultáneos. El timeout o la cancelación local pueden dejar una operación registrada. El formulario dirige a Mis subidas para comprobarla. El endpoint de subida carece de clave de idempotencia y el cliente no repite automáticamente el POST.

Mis subidas consulta cada cinco segundos si hay trabajo pendiente, en recuperación o en espera de reintento. La consulta se limita a la vista activa y cesa en estados terminales. Muestra intentos y fechas del catálogo y usa mensajes sanitizados. El reconciliador es el proceso que recupera la publicación. El navegador observa el resultado.

## Edición e historial

`PATCH /documents/{id}` aplica título, descripción, tipo y materias en una transacción. El historial usa la referencia actual del catálogo y conserva las versiones anteriores. Las descargas de versiones disponibles navegan al detalle académico y reutilizan su visor. La eliminación lógica `DELETE /documents/{id}` y la restauración `POST /documents/{id}/restore` piden confirmación.

## Verificación local

Actualiza el coordinador y usa el frontend según la [guía inicial](frontend-foundation.md). Esta fase usa la revisión vigente de Alembic y el seed existente.

```bash
cd frontend
npm ci
npm run dev
npm run lint
npm run typecheck
npm test
npm run build
```

Para E2E inicia `compose.rf3-test.yaml` con el proyecto `yachayrepo-professor-validation` y activa el perfil `reconciliation`. Usa el coordinador en `127.0.0.1:18000`, aplica la migración y seed en la base aislada y provisiona el administrador de pruebas. Exporta sus credenciales en `E2E_ADMIN_EMAIL` y `E2E_ADMIN_PASSWORD`.

```bash
VITE_API_BASE_URL=http://127.0.0.1:18000 E2E_PROFESSOR_FAILURES=1 npm run e2e -- --workers=1
```

La opción de fallos detiene y restaura un nodo del proyecto aislado para verificar reconciliación. Las cuentas y los documentos del escenario se crean con la API y se desactivan o eliminan lógicamente al terminar. La Fase 7.4 ampliará las pantallas de administración.
