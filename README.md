# Fault-Tolerant Distributed Institutional Repository

Academic prototype of a distributed repository for academic and administrative
documents. The project uses FastAPI, Docker, RabbitMQ, replicated storage nodes,
SQLite metadata, SHA-256 integrity checks, and JSON-based communication.

> This is a teaching and experimental prototype. Do not upload confidential or
> real institutional documents.

## Project objective

The project studies how replication and distributed storage can improve document
availability when storage nodes fail. The proposed system represents the six
academic schools of Yachay Tech University with six independent storage nodes.

The current implementation supports:

- a centralized FastAPI coordinator;
- six independent storage containers;
- synchronous replication across three nodes;
- document versioning and SHA-256 integrity verification;
- read failover when one replica is unavailable;
- RabbitMQ fanout events and an audit worker;
- JSON request and response validation experiments;
- measurements of message size, latency, processing time, and validation time.

Automatic replica repair, authentication, authorization, and production-grade
metadata fault tolerance remain future work.

## Repository structure

```text
Project/
├── README.md
├── .gitignore
├── docs/
│   ├── project-proposal.md
│   ├── reports/
│   │   └── workshop7-report.md
│   └── workshops/
│       ├── workshop6-description.md
│       └── workshop7-description.md
├── prototype/
│   ├── app/
│   ├── storage/
│   ├── worker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   └── README.md
└── experiments/
    └── json-interchange/
        ├── coordinator/
        ├── storage/
        ├── schemas/
        ├── experiments/
        │   └── results/
        ├── Dockerfile
        ├── requirements.txt
        └── README.md
```

The PDFs and `repository-workshop.zip` at the repository root are preserved as
historical academic material. The maintained source code is under `prototype/`
and `experiments/`.

## Run the distributed prototype

From `prototype/`:

```bash
docker compose up --build -d
docker compose ps
```

Available services:

- API and Swagger: <http://localhost:8000/docs>
- RabbitMQ management: <http://localhost:15674>

Basic checks:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/nodes
docker compose logs worker
```

See [`prototype/README.md`](prototype/README.md) for the document upload,
versioning, failover, and event tests.

Stop the environment without deleting its volumes:

```bash
docker compose down
```

Do not use `docker compose down -v` unless the local prototype data should be
deleted deliberately.

## Run the JSON interchange experiment

The JSON experiment uses two components on different machines or virtual
machines:

```text
Machine A: coordinator
Machine B: storage node
```

It evaluates two services:

1. `POST /schema/files`, with predefined JSON request and response schemas.
2. `POST /no-schema/files/info`, with manual checks and no predefined schema.

See
[`experiments/json-interchange/README.md`](experiments/json-interchange/README.md)
for deployment instructions and measurement commands.

## Documentation

- [Project proposal](docs/project-proposal.md)
- [Workshop 6 requirements](docs/workshops/workshop6-description.md)
- [Workshop 7 requirements](docs/workshops/workshop7-description.md)
- [Workshop 7 report](docs/reports/workshop7-report.md)
- [JSON experimental results](experiments/json-interchange/experiments/results/)

## Current limitations

- The metadata service is centralized.
- Automatic restoration of missing replicas is not implemented yet.
- Replica placement does not yet prioritize the document's academic school.
- The coordinator is designed for a single API process.
- External integrations, authentication, upload limits, and persistent retries
  are outside the current prototype.
- JSON Base64 transfer is intended for measurement and teaching, not efficient
  large-file production transfer.

## Suggested development roadmap

1. Consolidate school metadata and school-first replica placement.
2. Implement periodic node monitoring and persistent failure confirmation.
3. Add automatic replica recovery when the available replica count falls below
   the configured replication factor.
4. Add repeatable fault-injection and recovery tests.
5. Compare replication factors 1 and 3 using availability, latency, recovery
   time, and storage overhead.
6. Introduce authentication and authorization only after the storage and
   recovery behavior is stable.

