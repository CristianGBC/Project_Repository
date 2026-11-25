# Autenticación y autorización RBAC

YachayRepo protege la API académica con JWT y consulta PostgreSQL en cada
solicitud. Los roles no se consideran confiables por estar dentro del token:
las desactivaciones y cambios de membresía tienen efecto inmediatamente.

## Arquitectura

```text
Cliente -> JWT Bearer -> Coordinador -> RBAC PostgreSQL -> servicio documental
                                      -> RF=1/RF=3 -> nodos de almacenamiento

Monitor/reparador/reconciliador -> credenciales internas -> nodos
```

La autenticación académica y la interna son dominios separados:

- `Authorization: Bearer <JWT>` identifica usuarios ante el coordinador.
- `X-Storage-Token` autentica lecturas y escrituras coordinador–nodo.
- `X-Repair-Token` continúa reservado para reparación y reconciliación.

Un JWT académico no funciona directamente en un nodo y un token interno no es
aceptado como identidad académica.

## Modelo y migración

La migración `20261124_06`, posterior a `20261123_05`, amplía las tablas
existentes sin modificar migraciones históricas:

- `users`: nombre, apellido, `global_role`, cambio obligatorio de contraseña y
  `token_version`;
- `school_memberships`: varias filas únicas por usuario, escuela y rol;
- `documents.owner_id`: profesor propietario nullable;
- `security_audit_events`: actor, acción, recurso, resultado y fecha.

`created_by` conserva la trazabilidad original. `owner_id` diferencia propiedad
académica: los documentos históricos permanecen con propietario nulo, son
legibles por usuarios autenticados y solo administradores autorizados pueden
gestionarlos. Un profesor nunca se apropia implícitamente de contenido legacy.

## Roles y permisos

| Operación | STUDENT | PROFESSOR | SCHOOL_ADMIN | SUPER_ADMIN |
|---|---:|---:|---:|---:|
| Consultar/descargar publicados de cualquier escuela | Sí | Sí | Sí | Sí |
| Subir | No | Solo con `professor` en la escuela | No por el rol admin | Solo si además es `professor` |
| Nueva versión | No | Propia y membresía vigente | No por el rol admin | Solo bajo la regla de profesor |
| Gestionar documento | No | Propio | Su escuela | Todas |
| Crear estudiante/profesor | No | No | Su escuela | Sí |
| Otorgar SCHOOL_ADMIN/SUPER_ADMIN | No | No | No | Sí |
| Gestionar materias | No | No | Su escuela | Todas |
| Salud básica | No | No | Sí | Sí |
| Réplicas, hosts, déficits y auditoría | No | No | No | Sí |

Un usuario puede tener varios roles por escuela y roles diferentes entre
escuelas. `school_admin` no implica `professor`.

## Contraseñas, JWT y revocación

Las contraseñas se validan entre 12 y 1024 caracteres y se almacenan con
Argon2id mediante `argon2-cffi`. Nunca aparecen en respuestas o auditoría.

El access token contiene solamente identificadores y claims técnicos:
`sub`, `ver`, `iss`, `aud`, `iat`, `nbf`, `exp` y `jti`. El algoritmo permitido
es fijo (`HS256`). Se validan firma, expiración, emisor y audiencia. `ver` debe
coincidir con `users.token_version`; cambiar/restablecer contraseña o modificar
membresías invalida tokens anteriores. Una cuenta inactiva tampoco autoriza un
JWT ya emitido.

Una contraseña temporal establece `must_change_password=true`. Hasta cambiarla
solo se permiten `GET /auth/me` y `POST /auth/change-password`.

## Administrador inicial

Aplica migraciones y ejecuta una sola vez:

```bash
export BOOTSTRAP_ADMIN_EMAIL='admin@example.edu'
export BOOTSTRAP_ADMIN_FIRST_NAME='Nombre'
export BOOTSTRAP_ADMIN_LAST_NAME='Apellido'
python -m backend.auth.bootstrap_admin
```

Si `BOOTSTRAP_ADMIN_PASSWORD` no existe se solicita mediante entrada oculta. En
Docker:

```bash
docker compose -f infrastructure/docker/compose.yaml \
  --profile coordinator run --rm \
  -e BOOTSTRAP_ADMIN_EMAIL -e BOOTSTRAP_ADMIN_FIRST_NAME \
  -e BOOTSTRAP_ADMIN_LAST_NAME coordinator \
  python -m backend.auth.bootstrap_admin
```

El comando es idempotente: no duplica la cuenta, no cambia una contraseña ya
existente y no eleva silenciosamente una cuenta ordinaria.

## Endpoints para el frontend

### Autenticación

- `POST /auth/login`
- `GET /auth/me`
- `POST /auth/change-password`

No existe `POST /auth/register`.

```http
POST /auth/login
Content-Type: application/json

{"email":"profesor@example.edu","password":"contraseña larga"}
```

Respuesta abreviada:

```json
{
  "access_token": "<omitido>",
  "token_type": "bearer",
  "expires_at": "2026-11-25T01:00:00Z",
  "user": {
    "id": "00000000-0000-0000-0000-000000000000",
    "email": "profesor@example.edu",
    "global_role": null,
    "must_change_password": false,
    "memberships": [
      {"id":"00000000-0000-0000-0000-000000000001","school_id":"school-math","role":"professor","is_active":true}
    ]
  }
}
```

### Usuarios y membresías

- `GET/POST /users`
- `GET/PATCH /users/{user_id}`
- `POST /users/{user_id}/activate`
- `POST /users/{user_id}/deactivate`
- `POST /users/{user_id}/reset-password`
- `GET/POST /users/{user_id}/memberships`
- `DELETE /users/{user_id}/memberships/{membership_id}`

Los listados usan `offset`, `limit`, `search` e `is_active`. La creación de
usuario y membresías es transaccional. SCHOOL_ADMIN solo puede crear roles
`student` y `professor` en sus escuelas; los cambios globales corresponden a
SUPER_ADMIN. No se puede desactivar el último SUPER_ADMIN activo.

### Catálogos y documentos

- `GET /catalogs/schools`
- `GET /catalogs/document-types`
- `POST/PATCH /catalogs/document-types...` — SUPER_ADMIN
- `GET/POST/PATCH /subjects...` según rol
- endpoints `/documents` existentes, ahora autenticados;
- `PATCH /documents/{document_id}` para metadatos permitidos;
- `GET /documents/my/uploads` con versiones y reconciliación del propietario.

Las operaciones que transfieren bytes validan identidad, rol, escuela y
propiedad antes de crear documentos, versiones o reservas.

### Operación

- `GET /nodes` y `GET /operations/nodes`: salud básica administrativa;
- `GET /operations/hosts`;
- `GET /operations/replicas`;
- `GET /operations/deficits`;
- `GET /operations/reconciliation/jobs`;
- `GET /operations/reconciliation/jobs/{id}/attempts`;
- `GET /operations/security-audit`.

Los detalles operativos completos requieren SUPER_ADMIN y nunca exponen
secretos, hashes, cadenas de conexión o rutas físicas.

## Configuración

```dotenv
AUTH_JWT_SECRET=GENERAR_UN_SECRETO_ALEATORIO_DE_AL_MENOS_32_CARACTERES
AUTH_JWT_ALGORITHM=HS256
AUTH_ACCESS_TOKEN_EXPIRE_MINUTES=30
AUTH_JWT_ISSUER=yachayrepo
AUTH_JWT_AUDIENCE=yachayrepo-api
AUTH_ALLOWED_EMAIL_DOMAINS=
AUTH_LOGIN_MAX_ATTEMPTS=5
AUTH_LOGIN_WINDOW_SECONDS=300
CORS_ALLOWED_ORIGINS=https://repositorio.example.edu
STORAGE_INTERNAL_TOKEN=GENERAR_OTRO_SECRETO
```

`AUTH_ALLOWED_EMAIL_DOMAINS` es opcional y separado por comas. CORS no admite
`*` con credenciales. Los valores incluidos por defecto son exclusivamente de
desarrollo; producción debe inyectar secretos distintos y usar HTTPS.

## Integración distribuida

RBAC termina antes del servicio documental, por lo que no altera streaming,
SHA-256, anticolocación, prioridad de escuela, RF=1/RF=3, failover, reparación
o reconciliación. El reconciliador conserva `owner_id`, `school_id` y
`created_by` porque completa la misma versión y no crea una nueva identidad.

## Resultados E2E del 24 de noviembre de 2026

Con `compose.rf3-test.yaml`:

- bootstrap y login: HTTP 200;
- cuatro cuentas administradas: HTTP 201;
- estudiante, SCHOOL_ADMIN sin rol profesor y profesor fuera de escuela: HTTP
  403, con cero documentos/versiones/réplicas creados;
- subida de Matemáticas: propietario correcto, prioridad `node-math`, tres
  hosts distintos y SHA-256
  `6a93effb4393dffd795211221848e28f99d89a72bf8e68434f3fadde789b0c79`;
- estudiante de Biología descargó el documento de Matemáticas con el mismo
  SHA-256;
- al añadir rol profesor en Física, el mismo usuario subió con prioridad
  `node-phy`;
- con `node-math` apagado, failover autenticado devolvió HTTP 200 y hash
  idéntico;
- una subida parcial pasó de dos réplicas y `failed` a tres réplicas y
  `available`, conservando propietario y escuela;
- desactivar una cuenta invalidó su JWT vigente (HTTP 401);
- acceso directo anónimo o con JWT académico a un nodo devolvió HTTP 403;
- monitor, reparador y reconciliador continuaron activos con credenciales
  internas, sin cuentas académicas.

## Limitaciones

El limitador de login es deliberadamente sencillo y vive por proceso; un
despliegue con varios coordinadores debe añadir rate limiting compartido en el
proxy. Solo existe access token, sin refresh token. HS256 requiere custodiar el
secreto en todos los coordinadores. PostgreSQL sigue siendo punto único de
verdad y fallo. No se implementan OAuth, correo, recuperación por enlace,
frontend ni alta disponibilidad en esta fase.
