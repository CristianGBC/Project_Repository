# Gestión de materias y documentos

La clasificación por materia pertenece al documento completo, no a cada
versión. PostgreSQL almacena las materias y asociaciones; los bytes, versiones y
hashes SHA-256 permanecen intactos en los nodos Docker.

## Reglas

- `syllabus` y `study_material` requieren al menos una materia.
- `article`, `administrative` y documentos sin tipo permiten cero o más.
- Todas las materias asociadas deben estar activas y pertenecer a la misma
  escuela que el documento.
- Los documentos anteriores sin materias continúan disponibles.
- Desactivar una materia no elimina asociaciones ni documentos.

## Preparación segura en PostgreSQL

En la computadora que ejecuta PostgreSQL, primero respalda la base:

```bash
pg_dump --host=localhost --username=yachayrepo_app --format=custom \
  --file=yachayrepo_before_subjects_$(date +%F).dump yachayrepo_db
```

Revisa la revisión actual y aplica la migración desde un checkout actualizado:

```bash
cd backend
DATABASE_URL="$DATABASE_URL" alembic current
DATABASE_URL="$DATABASE_URL" alembic history
DATABASE_URL="$DATABASE_URL" alembic upgrade head
cd ..
DATABASE_URL="$DATABASE_URL" python -m backend.app.seed
```

La revisión esperada es `20261116_03`. No modifica archivos, versiones ni
réplicas existentes.

## Reconstruir el coordinador Dell

En la Dell, conserva el `.env` no versionado en la raíz del proyecto, con
`DATABASE_URL` apuntando al
PostgreSQL remoto y las seis `NODE_*_URL` apuntando al host de almacenamiento.
Después reconstruye únicamente el coordinador:

```bash
docker compose --env-file .env \
  -f infrastructure/docker/compose.yaml \
  --profile coordinator up --build -d coordinator

docker compose -f infrastructure/docker/compose.yaml logs -f coordinator
```

No uses `down -v`.

## Prueba manual en Swagger

Abre <http://localhost:8000/docs> en la Dell.

### 1. Crear Álgebra Lineal

Ejecuta `POST /subjects`:

```json
{
  "school_id": "school-math",
  "code": "MAT-201",
  "name": "Álgebra Lineal",
  "description": "Espacios vectoriales y transformaciones lineales"
}
```

Copia el `id` devuelto como `SUBJECT_ALGEBRA_ID`.

### 2. Crear Cálculo Numérico

Ejecuta otra vez `POST /subjects`:

```json
{
  "school_id": "school-math",
  "code": "MAT-305",
  "name": "Cálculo Numérico",
  "description": "Métodos numéricos y aproximación"
}
```

Copia el `id` como `SUBJECT_CALCULUS_ID`. Repetir `MAT-305` en la misma escuela
debe devolver HTTP 409.

### 3. Subir un documento con una materia

En `POST /documents`, usa:

```text
school_id: school-math
title: Sílabo de Álgebra Lineal
document_type_id: syllabus
subject_ids: SUBJECT_ALGEBRA_ID
file: seleccionar un PDF
```

Debe responder HTTP 201 y mostrar una materia. Un `syllabus` sin `subject_ids`
debe responder HTTP 400.

### 4. Subir material con dos materias

En otro `POST /documents`:

```text
school_id: school-math
title: Métodos matriciales para cálculo numérico
document_type_id: study_material
subject_ids: SUBJECT_ALGEBRA_ID
subject_ids: SUBJECT_CALCULUS_ID
file: seleccionar un PDF
```

Swagger representa `subject_ids` como una lista; agrega ambos UUID. La respuesta
debe contener las dos materias sin duplicar el documento.

### 5. Subir un artículo sin materias

En `POST /documents`:

```text
school_id: school-math
title: Artículo de investigación
document_type_id: article
subject_ids: dejar vacío
file: seleccionar un PDF
```

Debe responder HTTP 201 con `subjects: []`.

### 6. Consultar documentos de una materia

Ejecuta `GET /subjects/{subject_id}/documents` con
`SUBJECT_ALGEBRA_ID`. Los documentos eliminados lógicamente no aparecen.

### 7. Filtrar documentos

Ejecuta `GET /documents` combinando:

```text
school_id: school-math
subject_id: SUBJECT_ALGEBRA_ID
document_type_id: study_material
```

Todos los filtros son opcionales y pueden combinarse. Un documento con dos
materias aparece una sola vez.

### 8. Reemplazar asociaciones

Ejecuta `PUT /documents/{document_id}/subjects`:

```json
{
  "subject_ids": ["SUBJECT_CALCULUS_ID"]
}
```

La operación reemplaza el conjunto completo de forma transaccional. Para un
artículo puede enviarse `[]`; para `syllabus` o `study_material`, `[]` devuelve
HTTP 400.

### 9. Comprobar versiones

Antes y después del reemplazo consulta:

```text
GET /documents/{document_id}/versions
GET /documents/{document_id}/versions/{version_number}/download
```

Los IDs, números de versión, tamaños y SHA-256 deben permanecer iguales.

### 10. Desactivar una materia

Ejecuta `PATCH /subjects/{subject_id}`:

```json
{
  "is_active": false
}
```

Los documentos relacionados siguen existiendo, pero la materia ya no puede
asociarse a cargas o reemplazos nuevos.

## Verificación SQL

Conéctate al PostgreSQL remoto desde un equipo autorizado:

```bash
PGSSLMODE=require psql --host=POSTGRES_HOST --port=5432 \
  --username=yachayrepo_app --dbname=yachayrepo_db
```

Revisión y reglas de tipos:

```sql
SELECT version_num FROM alembic_version;

SELECT id, name, requires_subject, is_active
FROM document_types
ORDER BY id;
```

Materias:

```sql
SELECT id, school_id, code, name, description, is_active, created_at
FROM subjects
ORDER BY school_id, code;
```

Asociaciones:

```sql
SELECT
    ds.document_id,
    d.title,
    ds.subject_id,
    s.code,
    s.name
FROM document_subjects AS ds
JOIN documents AS d ON d.id = ds.document_id
JOIN subjects AS s ON s.id = ds.subject_id
ORDER BY d.title, s.code;
```

Documentos sin materias:

```sql
SELECT d.id, d.title, d.document_type_id
FROM documents AS d
LEFT JOIN document_subjects AS ds ON ds.document_id = d.id
WHERE ds.document_id IS NULL
  AND d.deleted_at IS NULL
ORDER BY d.created_at DESC;
```

Confirmar que las versiones no cambiaron:

```sql
SELECT document_id, version_number, filename, size_bytes, sha256, status
FROM document_versions
WHERE document_id = 'DOCUMENT_ID'
ORDER BY version_number;
```
