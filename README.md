# YachayRepo

YachayRepo es un proyecto académico de Sistemas Distribuidos para construir y
evaluar un repositorio institucional tolerante a fallos. La Etapa 3 permite
cargar, consultar y descargar documentos mediante el coordinador y los seis
nodos institucionales distribuidos entre dos computadoras Ubuntu.

> No se deben almacenar documentos institucionales reales, confidenciales o
> sensibles en este entorno académico.

## Arquitectura implementada

```mermaid
flowchart TD
    Client[Cliente] -->|HTTP puerto 8000| Coordinator[Coordinador FastAPI]
    Coordinator -->|GET /health| Bio[node-bio]
    Coordinator -->|GET /health| Phy[node-phy]
    Coordinator -->|GET /health| Math[node-math]
    Coordinator -->|GET /health| Chem[node-chem]
    Coordinator -->|GET /health| Earth[node-earth]
    Coordinator -->|GET /health| Agro[node-agro]
```

El **coordinador** es el único punto de acceso publicado al equipo anfitrión.
Su endpoint `GET /health` informa exclusivamente sobre el propio coordinador;
`GET /nodes` consulta los seis nodos concurrentemente mediante HTTPX y valida
la identidad de cada respuesta.

Un **nodo de almacenamiento** es un servicio FastAPI independiente que, en esta
etapa, conserva documentos y metadatos JSON dentro de su volumen. Los seis
contenedores ejecutan exactamente el mismo código. `NODE_ID` y `SCHOOL_NAME`
determinan cuál escuela representa cada instancia.

| Identificador | Escuela |
|---|---|
| `node-bio` | Escuela de Ciencias Biológicas e Ingeniería |
| `node-phy` | Escuela de Ciencias Físicas y Nanotecnología |
| `node-math` | Escuela de Ciencias Matemáticas y Computacionales |
| `node-chem` | Escuela de Ciencias Químicas e Ingeniería |
| `node-earth` | Escuela de Ciencias de la Tierra, Energía y Ambiente |
| `node-agro` | Escuela de Ciencias Agropecuarias y Agroindustriales |

Las identidades y URLs están centralizadas en `backend/app/config.py`. Cada URL
puede sustituirse mediante las variables `NODE_BIO_URL`, `NODE_PHY_URL`,
`NODE_MATH_URL`, `NODE_CHEM_URL`, `NODE_EARTH_URL` y `NODE_AGRO_URL`. El timeout
se configura con `NODE_REQUEST_TIMEOUT_SECONDS`.

### Nodos lógicos y físicos

Los seis contenedores son seis **nodos lógicos**, pero al ejecutarse localmente
comparten un solo equipo físico. En el futuro podrán distribuirse entre cuatro
Raspberry Pi cambiando sus URLs, sin modificar la lógica del coordinador. Cuando
se implemente `RF = 3`, la colocación deberá evitar que las tres réplicas de un
documento dependan del mismo dispositivo físico. Esa regla todavía no está
implementada.

## Estructura relevante

```text
Project_Repository/
├── backend/
│   ├── app/
│   │   ├── api/routes.py
│   │   ├── schemas/nodes.py
│   │   ├── services/node_health.py
│   │   ├── config.py
│   │   └── main.py
│   ├── storage/
│   │   ├── application.py
│   │   └── main.py
│   ├── tests/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── infrastructure/
│   ├── docker/
│   │   ├── compose.yaml
│   │   ├── .env.example
│   │   ├── Dockerfile.coordinator
│   │   └── Dockerfile.storage
│   └── raspberry/
├── frontend/
├── prototype/
├── experiments/
└── docs/
    ├── document-storage.md
    └── two-host-deployment.md
```

`prototype/` y `experiments/` conservan intactos los Workshops 6 y 7. La nueva
implementación no importa ni modifica esos servicios.

## Perfiles de despliegue

Compose dispone de perfiles independientes:

- `coordinator`: inicia únicamente el coordinador.
- `storage`: inicia únicamente los seis nodos.

Para una prueba local completa, activa ambos perfiles:

```bash
docker compose -f infrastructure/docker/compose.yaml \
  --profile coordinator --profile storage up --build -d
```

El coordinador queda disponible en <http://localhost:8000> y la documentación
interactiva en <http://localhost:8000/docs>. En modo local, el coordinador usa
los nombres de los servicios dentro de la red de Compose.

Para el despliegue distribuido, Host B publica los nodos en los puertos
`8001–8006`; Host A recibe sus URLs mediante variables de entorno. La guía
completa y los comandos separados para ambos equipos están en
[`docs/two-host-deployment.md`](docs/two-host-deployment.md).

La arquitectura documental, endpoints, persistencia e integridad se detallan en
[`docs/document-storage.md`](docs/document-storage.md).

## Operaciones documentales

Cargar un archivo en la escuela matemática:

```bash
curl -X POST http://localhost:8000/documents \
  -F 'school_id=node-math' \
  -F 'file=@./documento.pdf'
```

Consultar y descargar usando el UUID devuelto:

```bash
curl http://localhost:8000/documents/node-math/DOCUMENT_ID
curl -OJ http://localhost:8000/documents/node-math/DOCUMENT_ID/download
```

El coordinador y el nodo calculan SHA-256 de forma independiente. Antes de cada
descarga, ambos vuelven a verificar el contenido. El factor temporal es RF = 1:
cada documento existe únicamente en el nodo asociado a su escuela.

Verificar la API:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/nodes
```

Consultar contenedores y logs:

```bash
docker compose -f infrastructure/docker/compose.yaml ps
docker compose -f infrastructure/docker/compose.yaml logs coordinator
docker compose -f infrastructure/docker/compose.yaml logs node-math
```

Detener el sistema sin eliminar sus seis volúmenes:

```bash
docker compose -f infrastructure/docker/compose.yaml \
  --profile coordinator --profile storage down
```

No se debe usar `down -v` durante las pruebas normales porque elimina los
volúmenes preparados para cada nodo.

## Prueba manual de fallo

1. Levanta el sistema y confirma inicialmente los seis estados:

   ```bash
   curl http://localhost:8000/nodes
   ```

2. Detén el nodo matemático y vuelve a consultar:

   ```bash
   docker compose -f infrastructure/docker/compose.yaml stop node-math
   curl http://localhost:8000/nodes
   ```

   `node-math` debe aparecer como `unavailable`; los otros cinco deben continuar
   como `healthy`.

3. Inicia el nodo y verifica su recuperación:

   ```bash
   docker compose -f infrastructure/docker/compose.yaml start node-math
   curl http://localhost:8000/nodes
   ```

## Pruebas automatizadas

Usando el entorno virtual creado en la raíz:

```bash
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
python -m pytest backend/tests
```

Las pruebas no requieren Docker ni Raspberry Pi. Utilizan el transporte de
prueba de HTTPX para simular respuestas correctas, conexiones fallidas,
timeouts, HTTP 500, JSON inválido e identidades incorrectas. También verifican
que las seis consultas se inicien concurrentemente.

## Comportamiento actual

- `GET /health` del coordinador responde aunque todos los nodos estén caídos.
- `GET /nodes` siempre conserva los seis nodos y el orden configurado.
- Un nodo solo se clasifica como `healthy` si devuelve HTTP satisfactorio, JSON
  válido, `status=ok`, `service=storage` y el identificador esperado.
- Cada nodo posee un volumen Docker independiente montado en `/data`.
- Los perfiles permiten ejecutar coordinador y almacenamiento en hosts
  diferentes sin dependencias obligatorias entre servicios.
- Las URLs remotas y el timeout llegan al contenedor coordinador mediante
  variables de entorno configurables.
- Las imágenes se basan en Python `slim`, sin dependencias específicas de x86 ni
  una plataforma `linux/amd64` forzada, para permitir builds ARM64 posteriores.

## Funcionalidades todavía no implementadas

Esta etapa no incluye frontend, PostgreSQL, catálogo central de metadatos,
versionamiento, replicación, RabbitMQ, monitoreo periódico, recuperación
automática, autenticación, autorización, cifrado, balanceo de carga ni
despliegue físico sobre Raspberry Pi.

La siguiente etapa deberá incorporar PostgreSQL como catálogo central sin mover
los archivos fuera de sus nodos. Después de estabilizar ese catálogo podrá
ampliarse el flujo actual a replicación RF = 3 y recuperación de copias.
