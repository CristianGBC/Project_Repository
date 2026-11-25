# Frontend de YachayRepo — Fase 7.1

## Desarrollo local

Desde la raíz, configura el coordinador con su PostgreSQL migrado y su cuenta administrativa existente. En el entorno que carga el coordinador establece `CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173` (conserva cualquier otro origen autorizado necesario). Al usar Compose, recrea solo `coordinator` con el archivo de entorno y perfil habituales para aplicar el cambio.

```bash
cd frontend
npm ci
cp .env.example .env
npm run dev
```

Abre <http://localhost:5173/login>. `VITE_API_BASE_URL` es público y apunta al coordinador, por defecto `http://localhost:8000`. Usa la URL alcanzable desde el navegador. Mantén los secretos de JWT, PostgreSQL y de los nodos en el backend. El frontend se ejecuta independientemente de Docker.

## Arquitectura e identidad

React 19, TypeScript estricto, Vite, Tailwind CSS, primitivas Radix y variantes CVA al estilo shadcn/ui, iconos Lucide, Axios, TanStack Router y Query, React Hook Form y Zod. Las versiones exactas están en [package.json](../frontend/package.json) y el lockfile.

- `src/api/session.ts` concentra Bearer, ciclo de vida de sesión, claves de consulta y errores HTTP.
- `src/routes/router.tsx` contiene rutas, guards y estados de error. `src/features/` contiene formularios, permisos y pantallas.
- `src/components/ui.tsx` contiene componentes visuales reutilizados por el layout y los formularios. `src/styles.css` centraliza tokens y modo claro.

La marca tipográfica «YachayRepo» se acompaña de un icono de libro de Lucide. La paleta azul es provisional. El encabezado azul oscuro del [plan institucional Yachay Tech](https://yachaytech.edu.ec/wp-content/uploads/2026/01/pedi_yachay_tech_2026-2029.pdf) sirve de referencia visual. Los valores CSS son propios de esta interfaz.

## Sesión, rutas y permisos

`POST /auth/login` recibe correo y contraseña. `GET /auth/me` valida el token y devuelve la cuenta y sus membresías. `GET /catalogs/schools` resuelve nombres desde el catálogo. El perfil muestra datos de lectura. Inicio y perfil son las únicas entradas de navegación general.

`/login` es pública. `/`, `/profile` y `/change-password` esperan la validación de `/auth/me`. Una cuenta con `must_change_password` solo puede entrar en la pantalla de cambio. `POST /auth/change-password` recibe la contraseña actual y la nueva, con 12–1024 caracteres para la nueva. La confirmación se valida localmente. El backend invalida el JWT anterior y se solicita un nuevo inicio de sesión. Hay vista 403 y vista para rutas desconocidas.

El token y su vencimiento se guardan en memoria y `sessionStorage` de la pestaña, con acceso centralizado. La persistencia permite recargar la página. JavaScript del mismo origen puede leer el token si hay XSS. La expiración, un 401 autenticado o el cierre local cancelan consultas privadas, vacían la caché y terminan la sesión. Un 403, un 503 o un fallo de red conservan la sesión. El backend emite access tokens únicamente y el cierre local deja vigente el token en el servidor hasta expiración o invalidación.

Los permisos de interfaz operan sobre membresías activas por escuela. La administración escolar, la administración global y el permiso de profesor se evalúan independientemente. FastAPI conserva la autorización efectiva. Las secciones futuras se incorporarán junto con sus rutas.

Axios aplica diez segundos a solicitudes de esta fase. Query usa 30 segundos de frescura, cinco minutos de retención, un reintento para fallos de red o servidor y cero reintentos automáticos para escrituras. La interfaz presenta mensajes en español sin reproducir detalles internos.

## Pruebas

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

Playwright utiliza el coordinador aislado de `compose.rf3-test.yaml` en `http://127.0.0.1:18000`. Arranca `postgres-test` y `coordinator-test` en un proyecto Compose exclusivo, aplica Alembic y seed en la base que termina en `_test`, y crea un superadministrador con `python -m backend.auth.bootstrap_admin` dentro del coordinador de prueba. Exporta `E2E_ADMIN_EMAIL` y `E2E_ADMIN_PASSWORD` de esa cuenta en tu terminal. Ejecuta en `frontend/`:

```bash
npx playwright install chromium
VITE_API_BASE_URL=http://127.0.0.1:18000 npm run e2e
```

La suite crea cuentas temporales mediante la API administrativa y las desactiva al finalizar. Los escenarios cubren autenticación, membresías, recuperación tras corte de red, revocación y navegación móvil.

## Fase 7.2

El visor deberá solicitar bytes al coordinador con Bearer. Define un timeout específico de transferencia, considera memoria de PDFs grandes y revoca las Blob URLs al reemplazar el archivo o desmontar el visor.
