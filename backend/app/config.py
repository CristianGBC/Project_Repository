"""Environment-driven configuration for the coordinator."""

from dataclasses import dataclass
import os
from uuid import UUID


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
    transfer_timeout_seconds: float
    max_upload_size_bytes: int
    database_url: str | None = None
    technical_user_id: UUID = UUID("7d14ef5d-3c61-4ac8-90b5-2bd3f0aa0431")
    technical_user_email: str = "development-operations@yachayrepo.invalid"
    technical_user_password_hash: str = "!authentication-disabled!"


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
    transfer_timeout = float(os.getenv("DOCUMENT_TRANSFER_TIMEOUT_SECONDS", "30.0"))
    if transfer_timeout <= 0:
        raise ValueError("DOCUMENT_TRANSFER_TIMEOUT_SECONDS must be greater than zero")
    max_upload_mb = float(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
    if max_upload_mb <= 0:
        raise ValueError("MAX_UPLOAD_SIZE_MB must be greater than zero")
    return CoordinatorSettings(
        nodes=nodes,
        request_timeout_seconds=timeout,
        transfer_timeout_seconds=transfer_timeout,
        max_upload_size_bytes=int(max_upload_mb * 1024 * 1024),
        database_url=os.getenv("DATABASE_URL"),
        technical_user_id=UUID(
            os.getenv(
                "TECHNICAL_USER_ID",
                "7d14ef5d-3c61-4ac8-90b5-2bd3f0aa0431",
            )
        ),
        technical_user_email=os.getenv(
            "TECHNICAL_USER_EMAIL",
            "development-operations@yachayrepo.invalid",
        ),
        technical_user_password_hash=os.getenv(
            "TECHNICAL_USER_PASSWORD_HASH",
            "!authentication-disabled!",
        ),
    )
