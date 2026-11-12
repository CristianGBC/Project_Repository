# YachayRepo

YachayRepo es el nombre provisional de un proyecto universitario de Sistemas
Distribuidos orientado a la gestión documental. Este repositorio reúne el
prototipo y los experimentos de workshops anteriores, y establece una
estructura separada para desarrollar progresivamente la aplicación integrada.

> El proyecto es académico y experimental. No se deben cargar documentos
> institucionales reales, confidenciales o sensibles.

## Alcance previsto

La arquitectura contemplada para la aplicación definitiva incluye:

- un frontend web;
- un backend basado en FastAPI;
- un servicio centralizado de metadatos;
- seis nodos lógicos de almacenamiento;
- contenedores Docker asociados a las escuelas, sin asignar todavía nombres
  oficiales;
- replicación con factor de replicación `RF = 3`;
- detección de fallos en los nodos;
- recuperación automática de réplicas; y
- un futuro despliegue distribuido sobre cuatro Raspberry Pi.

Estos elementos describen el objetivo de desarrollo. La creación de esta
estructura no implica que las funcionalidades ya estén implementadas.

## Organización del repositorio

```text
Project_Repository/
├── frontend/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── database/
│   ├── storage/
│   ├── monitoring/
│   ├── recovery/
│   └── tests/
├── infrastructure/
│   ├── docker/
│   └── raspberry/
├── experiments/
│   └── json-interchange/
├── prototype/
├── docs/
├── .gitignore
└── README.md
```

### Aplicación definitiva

- `frontend/`: futura interfaz web del sistema.
- `backend/app/api/`: futuros endpoints y rutas HTTP del backend.
- `backend/app/models/`: futuros modelos internos y de persistencia.
- `backend/app/schemas/`: futuros esquemas de validación e intercambio de datos.
- `backend/app/services/`: futura lógica de aplicación y coordinación.
- `backend/app/database/`: futura integración con el servicio centralizado de
  metadatos.
- `backend/storage/`: futura lógica de los seis nodos lógicos de almacenamiento.
- `backend/monitoring/`: futura detección y seguimiento de fallos.
- `backend/recovery/`: futura recuperación automática de réplicas.
- `backend/tests/`: futuras pruebas automatizadas de la aplicación integrada.
- `infrastructure/docker/`: futura definición de contenedores y despliegues
  locales asociados a las escuelas.
- `infrastructure/raspberry/`: futura documentación y recursos para el
  despliegue sobre cuatro Raspberry Pi.
- `docs/`: documentación técnica y académica de la aplicación integrada.

Las carpetas todavía vacías contienen archivos `.gitkeep` para conservar la
estructura en Git. En esta etapa no se incluyen dependencias ni configuraciones
funcionales nuevas.

### Trabajo anterior preservado

- `prototype/`: prototipo distribuido del Workshop 6, con su API, nodos de
  almacenamiento, worker y configuración Docker existentes.
- `experiments/json-interchange/`: experimentos del Workshop 7 sobre
  intercambio y validación de mensajes JSON, incluidas sus mediciones y
  resultados.

Estas implementaciones se mantienen en sus rutas originales y funcionan como
referencia histórica. El desarrollo nuevo debe realizarse en `frontend/`,
`backend/` e `infrastructure/`, evitando mezclarlo con los servicios de los
workshops.

## Ejecutar el prototipo existente

Desde `prototype/`:

```bash
docker compose up --build -d
docker compose ps
```

- API y Swagger: <http://localhost:8000/docs>
- Administración de RabbitMQ: <http://localhost:15674>

Las instrucciones completas están en
[`prototype/README.md`](prototype/README.md). Para detener el entorno sin
eliminar sus volúmenes:

```bash
docker compose down
```

## Ejecutar el experimento JSON existente

El experimento separa un coordinador y un nodo de almacenamiento para comparar
intercambios con y sin esquemas JSON. Sus instrucciones y resultados están en
[`experiments/json-interchange/README.md`](experiments/json-interchange/README.md).

## Estado actual

La estructura base de la aplicación definitiva está preparada, pero sus
componentes permanecen vacíos deliberadamente. Las funcionalidades se
incorporarán de manera progresiva en etapas posteriores.
