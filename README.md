# YachayRepo

YachayRepo es el nombre provisional de un proyecto universitario de Sistemas
Distribuidos orientado a la gestión de documentos académicos y administrativos.
El objetivo es construir y evaluar progresivamente un repositorio distribuido
con replicación, detección de fallos y recuperación automática de réplicas.

> El sistema se encuentra en una etapa inicial. La estructura de la aplicación
> definitiva está preparada, pero todavía no contiene implementaciones
> funcionales. No se deben almacenar documentos institucionales reales o
> confidenciales en los prototipos.

## Arquitectura prevista

El diseño del proyecto contempla los siguientes componentes:

- un frontend web para gestionar y consultar documentos;
- un backend desarrollado con FastAPI;
- un servicio centralizado de metadatos;
- seis nodos lógicos de almacenamiento, asociados a las seis escuelas sin
  codificar sus nombres en esta etapa;
- contenedores Docker para representar y aislar los nodos de almacenamiento;
- replicación de documentos con factor de replicación `RF = 3`;
- detección de fallos en los nodos de almacenamiento;
- recuperación automática para restablecer las réplicas perdidas;
- un futuro despliegue distribuido sobre cuatro Raspberry Pi.

La arquitectura se implementará de manera incremental. La creación de esta
estructura no implica que todos estos componentes estén disponibles todavía.

## Estructura del repositorio

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

Los directorios todavía vacíos contienen archivos `.gitkeep` para que Git pueda
conservar la estructura inicial sin introducir configuraciones o código antes de
que sean necesarios.

## Responsabilidad de cada carpeta

### `frontend/`

Aplicación web futura para interacción con usuarios, carga, búsqueda y descarga
de documentos. En esta etapa permanece vacía y no se ha seleccionado ni
configurado una implementación.

### `backend/`

Aplicación backend definitiva. Su organización inicial separa las siguientes
responsabilidades:

- `app/api/`: endpoints y rutas HTTP de FastAPI;
- `app/models/`: modelos internos y entidades del dominio;
- `app/schemas/`: esquemas de solicitudes y respuestas;
- `app/services/`: reglas y casos de uso de la aplicación;
- `app/database/`: acceso al servicio centralizado de metadatos;
- `storage/`: comunicación y operaciones de los nodos de almacenamiento;
- `monitoring/`: detección y registro del estado de los nodos;
- `recovery/`: restauración futura del factor de replicación;
- `tests/`: pruebas automatizadas del backend y de sus servicios distribuidos.

Estas carpetas definen límites iniciales, no implementaciones ni contratos
definitivos.

### `infrastructure/`

Recursos futuros para desplegar el sistema:

- `docker/`: imágenes, composición y configuración local de contenedores;
- `raspberry/`: documentación y recursos para el futuro despliegue sobre cuatro
  Raspberry Pi.

Actualmente no contiene configuración funcional.

### `prototype/`

Prototipo preservado del Workshop 6. Incluye un coordinador FastAPI, seis nodos
de almacenamiento simulados mediante Docker, replicación básica, metadatos en
SQLite y publicación de eventos mediante RabbitMQ.

Este código constituye evidencia y aprendizaje previo. No debe confundirse con
la implementación definitiva ubicada en `backend/` e `infrastructure/`.

Para conocer su alcance y ejecución, consulta
[`prototype/README.md`](prototype/README.md).

### `experiments/`

Experimentos preservados del Workshop 7. El directorio
`experiments/json-interchange/` contiene las pruebas de intercambio de datos
JSON con y sin esquemas predefinidos, además de mediciones de tamaño, latencia y
sobrecarga de procesamiento.

Para reproducirlos, consulta
[`experiments/json-interchange/README.md`](experiments/json-interchange/README.md).

### `docs/`

Documentación futura de arquitectura, decisiones técnicas, despliegue,
experimentos y evaluación del proyecto. En esta etapa solo se conserva el
directorio para organizar documentación posterior.

## Estado actual

| Área | Estado |
|---|---|
| Prototipo del Workshop 6 | Preservado |
| Experimentos del Workshop 7 | Preservados |
| Estructura de la aplicación definitiva | Preparada |
| Frontend definitivo | No implementado |
| Backend definitivo | No implementado |
| Servicio definitivo de metadatos | No implementado |
| Detección definitiva de fallos | No implementada |
| Recuperación automática de réplicas | No implementada |
| Despliegue sobre Raspberry Pi | Planificado |

## Principios para el desarrollo

- Implementar cambios pequeños y verificables.
- Mantener separados el código definitivo, los prototipos y los experimentos.
- No modificar retrospectivamente los workshops para adaptarlos a la nueva
  arquitectura.
- Documentar las decisiones antes de introducir infraestructura compleja.
- Evitar datos sensibles, credenciales y conexiones a entornos de producción.
- Agregar dependencias y configuraciones únicamente cuando una funcionalidad
  concreta las requiera.

## Próximos pasos posibles

1. Definir los primeros requisitos funcionales de la aplicación integrada.
2. Documentar las responsabilidades y contratos entre componentes.
3. Diseñar el modelo inicial del servicio de metadatos.
4. Definir el contrato entre el coordinador y los nodos de almacenamiento.
5. Preparar pruebas locales antes de introducir el despliegue físico.
6. Diseñar posteriormente la distribución sobre cuatro Raspberry Pi.

Estos pasos son orientativos y no representan funcionalidades implementadas en
la organización inicial del repositorio.
