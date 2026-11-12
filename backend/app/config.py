"""Environment-driven configuration for the coordinator."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class NodeSettings:
    """Identity and network location of one logical storage node."""

    node_id: str
    school: str
    url: str


@dataclass(frozen=True)
class CoordinatorSettings:
    """Settings used by the coordinator application."""

    nodes: tuple[NodeSettings, ...]
    request_timeout_seconds: float


NODE_IDENTITIES: tuple[tuple[str, str, str], ...] = (
    (
        "node-bio",
        "Escuela de Ciencias Biológicas e Ingeniería",
        "NODE_BIO_URL",
    ),
    (
        "node-phy",
        "Escuela de Ciencias Físicas y Nanotecnología",
        "NODE_PHY_URL",
    ),
    (
        "node-math",
        "Escuela de Ciencias Matemáticas y Computacionales",
        "NODE_MATH_URL",
    ),
    (
        "node-chem",
        "Escuela de Ciencias Químicas e Ingeniería",
        "NODE_CHEM_URL",
    ),
    (
        "node-earth",
        "Escuela de Ciencias de la Tierra, Energía y Ambiente",
        "NODE_EARTH_URL",
    ),
    (
        "node-agro",
        "Escuela de Ciencias Agropecuarias y Agroindustriales",
        "NODE_AGRO_URL",
    ),
)


def load_settings() -> CoordinatorSettings:
    """Load node URLs and timeout without coupling them to endpoint logic."""

    nodes = tuple(
        NodeSettings(
            node_id=node_id,
            school=school,
            url=os.getenv(url_variable, f"http://{node_id}:8000").rstrip("/"),
        )
        for node_id, school, url_variable in NODE_IDENTITIES
    )
    timeout = float(os.getenv("NODE_REQUEST_TIMEOUT_SECONDS", "2.0"))
    if timeout <= 0:
        raise ValueError("NODE_REQUEST_TIMEOUT_SECONDS must be greater than zero")
    return CoordinatorSettings(nodes=nodes, request_timeout_seconds=timeout)
