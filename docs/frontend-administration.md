# Administración de YachayRepo — Fase 7.4

## Alcance y rutas

| Función | Rutas | Alcance |
|---|---|---|
| Cuentas y membresías | `/admin/users`, `/admin/users/new`, `/admin/users/$userId`, `/memberships` bajo el detalle | Global o escolar |
| Edición global | `/admin/users/$userId/edit` | Superadministración |
| Materias | `/admin/subjects` | Global o escolar |
| Tipos documentales | `/admin/document-types` | Superadministración |
| Documentos | `/admin/documents`, `/admin/documents/$documentId`, `/edit` bajo el detalle | Global o escolar |
| Auditoría | `/admin/audit` | Superadministración |

La SPA reusa el cliente autenticado, el sidebar y TanStack Query. Las pantallas administrativas se cargan bajo demanda. Los guardas consultan la sesión y FastAPI aplica la autorización al operar sobre cada recurso. Una membresía administrativa por sí sola no habilita subidas.

## Usuarios y credenciales

`/users` pagina por servidor y admite nombre o correo, estado, escuela y rol. El administrador escolar puede crear cuentas con roles docente o estudiante en sus escuelas. El superadministrador puede conceder privilegios administrativos al crear cuentas o asignar membresías escolares. La edición global y la activación o desactivación corresponden al superadministrador. El backend protege al último superadministrador activo. El panel oculta acciones de desactivación, restablecimiento y cambio de roles sobre la propia cuenta.

Las cuentas compartidas muestran únicamente las membresías del ámbito escolar del actor. `can_reset_password` describe si el actor puede restablecer la contraseña según todas las membresías de la cuenta. Revocar una membresía invalida el JWT anterior. Las contraseñas temporales viven en los campos del formulario durante la operación, respetan 12–1024 caracteres y requieren cambio al entrar por primera vez o después de un restablecimiento.

## Catálogos, documentos y auditoría

Materias usa `/subjects` con creación y edición dentro de la escuela autorizada. Se puede activar o desactivar la materia. El catálogo global usa `/catalogs/document-types` para crear, editar y cambiar disponibilidad. Las mutaciones invalidan los catálogos de las vistas académicas y docentes.

`GET /documents/admin/list` consulta documentos del alcance autorizado, incluidos los eliminados cuando se solicita. Admite escuela, tipo, búsqueda por título, identificador y paginación. Incluye metadatos y el nombre disponible del propietario. Los documentos históricos sin propietario conservan esa condición. Las publicaciones vigentes pueden abrirse en el visor académico. Editar metadatos, eliminar lógicamente y restaurar reutilizan los endpoints existentes. La edición requiere una publicación vigente con versión disponible.

`GET /operations/security-audit` admite filtros exactos por acción, actor, recurso y día UTC, además de paginación. Muestra identificadores, resultado y marcas de tiempo. El detalle libre de auditoría queda reservado. Esta vista cubre los eventos de seguridad que actualmente registra el backend.

## Ejecución y pruebas

Actualiza el coordinador y habilita el origen local indicado en la [guía de la Fase 7.1](frontend-foundation.md). Usa la revisión Alembic vigente.

```bash
cd frontend
npm ci
npm run dev
npm run lint
npm run typecheck
npm test
npm run build
```

Prueba `http://localhost:5173/admin/users` con una cuenta autorizada. Para Playwright usa el proyecto Compose aislado `yachayrepo-admin-validation`, los servicios de `compose.rf3-test.yaml` y las credenciales temporales de su administrador, exportadas como `E2E_ADMIN_EMAIL` y `E2E_ADMIN_PASSWORD`.

```bash
VITE_API_BASE_URL=http://127.0.0.1:18000 npm run e2e -- --workers=1
```

Los escenarios administrativos mutan exclusivamente el entorno de pruebas. La Fase 7.5 añade el panel de infraestructura y el seguimiento operativo distribuido.
