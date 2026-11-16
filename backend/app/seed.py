"""Idempotently register the initial schools, physical hosts, and storage nodes."""

import os

from sqlalchemy.dialects.postgresql import insert

from backend.app.database import create_database_engine, create_session_factory
from backend.app.models import DocumentType, PhysicalHost, School, StorageNode

DOCUMENT_TYPES = (
    {"id": "article", "code": "article", "name": "Artículo"},
    {
        "id": "study_material",
        "code": "study_material",
        "name": "Material de estudio",
    },
    {"id": "syllabus", "code": "syllabus", "name": "Syllabus"},
    {
        "id": "administrative",
        "code": "administrative",
        "name": "Documento administrativo",
    },
)

SCHOOLS = (
    {
        "id": "school-bio",
        "code": "BIO",
        "name": "Escuela de Ciencias Biológicas e Ingeniería",
    },
    {
        "id": "school-phy",
        "code": "PHY",
        "name": "Escuela de Ciencias Físicas y Nanotecnología",
    },
    {
        "id": "school-math",
        "code": "MATH",
        "name": "Escuela de Ciencias Matemáticas y Computacionales",
    },
    {
        "id": "school-chem",
        "code": "CHEM",
        "name": "Escuela de Ciencias Químicas e Ingeniería",
    },
    {
        "id": "school-earth",
        "code": "EARTH",
        "name": "Escuela de Ciencias de la Tierra, Energía y Ambiente",
    },
    {
        "id": "school-agro",
        "code": "AGRO",
        "name": "Escuela de Ciencias Agropecuarias y Agroindustriales",
    },
)

HOST_IDS = ("raspberry-1", "raspberry-2", "raspberry-3", "raspberry-4")

NODES = (
    {
        "id": "node-bio",
        "home_school_id": "school-bio",
        "physical_host_id": "raspberry-1",
        "port": 8001,
    },
    {
        "id": "node-phy",
        "home_school_id": "school-phy",
        "physical_host_id": "raspberry-1",
        "port": 8002,
    },
    {
        "id": "node-math",
        "home_school_id": "school-math",
        "physical_host_id": "raspberry-2",
        "port": 8003,
    },
    {
        "id": "node-chem",
        "home_school_id": "school-chem",
        "physical_host_id": "raspberry-3",
        "port": 8004,
    },
    {
        "id": "node-earth",
        "home_school_id": "school-earth",
        "physical_host_id": "raspberry-3",
        "port": 8005,
    },
    {
        "id": "node-agro",
        "home_school_id": "school-agro",
        "physical_host_id": "raspberry-4",
        "port": 8006,
    },
)


def seed_initial_catalog() -> None:
    """Insert only missing rows so existing operator-managed values remain intact."""

    engine = create_database_engine()
    session_factory = create_session_factory(engine)
    hosts = [
        {
            "id": host_id,
            "hostname": os.getenv(
                f"{host_id.replace('-', '_').upper()}_HOSTNAME", host_id
            ),
            "device_type": "raspberry_pi",
        }
        for host_id in HOST_IDS
    ]
    try:
        with session_factory.begin() as session:
            session.execute(
                insert(DocumentType).values(DOCUMENT_TYPES).on_conflict_do_nothing()
            )
            session.execute(insert(School).values(SCHOOLS).on_conflict_do_nothing())
            session.execute(insert(PhysicalHost).values(hosts).on_conflict_do_nothing())
            session.execute(insert(StorageNode).values(NODES).on_conflict_do_nothing())
    finally:
        engine.dispose()


if __name__ == "__main__":
    seed_initial_catalog()
