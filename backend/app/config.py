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
    replication_factor: int = 3
    health_check_interval_seconds: float = 5.0
    health_check_timeout_seconds: float = 2.0
    health_failure_threshold: int = 3
    health_recovery_threshold: int = 2
    repair_enabled: bool = True
    repair_interval_seconds: float = 10.0
    repair_down_grace_seconds: float = 60.0
    repair_max_concurrent_jobs: int = 2
    repair_transfer_timeout_seconds: float = 120.0
    repair_retry_backoff_seconds: float = 30.0
    repair_excess_cleanup_enabled: bool = True
    repair_internal_token: str = "!development-repair-token!"
    reconciliation_enabled: bool = True
    reconciliation_interval_seconds: float = 15.0
    reconciliation_retry_backoff_seconds: float = 30.0
    reconciliation_max_attempts: int = 10
    reconciliation_max_concurrent_jobs: int = 2
    reconciliation_transfer_timeout_seconds: float = 120.0
    database_url: str | None = None
    technical_user_id: UUID = UUID("7d14ef5d-3c61-4ac8-90b5-2bd3f0aa0431")
    technical_user_email: str = "development-operations@yachayrepo.invalid"
    technical_user_password_hash: str = "!authentication-disabled!"

    def __post_init__(self) -> None:
        if self.replication_factor not in {1, 3}:
            raise ValueError("REPLICATION_FACTOR must be either 1 or 3")
        if self.health_check_interval_seconds <= 0:
            raise ValueError("HEALTH_CHECK_INTERVAL_SECONDS must be greater than zero")
        if self.health_check_timeout_seconds <= 0:
            raise ValueError("HEALTH_CHECK_TIMEOUT_SECONDS must be greater than zero")
        if self.health_failure_threshold < 1:
            raise ValueError("HEALTH_FAILURE_THRESHOLD must be at least one")
        if self.health_recovery_threshold < 1:
            raise ValueError("HEALTH_RECOVERY_THRESHOLD must be at least one")
        if self.repair_interval_seconds <= 0:
            raise ValueError("REPAIR_INTERVAL_SECONDS must be greater than zero")
        if self.repair_down_grace_seconds < 0:
            raise ValueError("REPAIR_DOWN_GRACE_SECONDS must not be negative")
        if self.repair_max_concurrent_jobs < 1:
            raise ValueError("REPAIR_MAX_CONCURRENT_JOBS must be at least one")
        if self.repair_transfer_timeout_seconds <= 0:
            raise ValueError(
                "REPAIR_TRANSFER_TIMEOUT_SECONDS must be greater than zero"
            )
        if self.repair_retry_backoff_seconds < 0:
            raise ValueError("REPAIR_RETRY_BACKOFF_SECONDS must not be negative")
        if not self.repair_internal_token:
            raise ValueError("REPAIR_INTERNAL_TOKEN must not be empty")
        if self.reconciliation_interval_seconds <= 0:
            raise ValueError(
                "RECONCILIATION_INTERVAL_SECONDS must be greater than zero"
            )
        if self.reconciliation_retry_backoff_seconds < 0:
            raise ValueError(
                "RECONCILIATION_RETRY_BACKOFF_SECONDS must not be negative"
            )
        if self.reconciliation_max_attempts < 1:
            raise ValueError("RECONCILIATION_MAX_ATTEMPTS must be at least one")
        if self.reconciliation_max_concurrent_jobs < 1:
            raise ValueError(
                "RECONCILIATION_MAX_CONCURRENT_JOBS must be at least one"
            )
        if self.reconciliation_transfer_timeout_seconds <= 0:
            raise ValueError(
                "RECONCILIATION_TRANSFER_TIMEOUT_SECONDS must be greater than zero"
            )


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
    try:
        replication_factor = int(os.getenv("REPLICATION_FACTOR", "3"))
    except ValueError as error:
        raise ValueError("REPLICATION_FACTOR must be either 1 or 3") from error
    if replication_factor not in {1, 3}:
        raise ValueError("REPLICATION_FACTOR must be either 1 or 3")
    try:
        health_interval = float(os.getenv("HEALTH_CHECK_INTERVAL_SECONDS", "5"))
        health_timeout = float(os.getenv("HEALTH_CHECK_TIMEOUT_SECONDS", "2"))
        failure_threshold = int(os.getenv("HEALTH_FAILURE_THRESHOLD", "3"))
        recovery_threshold = int(os.getenv("HEALTH_RECOVERY_THRESHOLD", "2"))
    except ValueError as error:
        raise ValueError("health monitor settings must be numeric") from error
    try:
        repair_interval = float(os.getenv("REPAIR_INTERVAL_SECONDS", "10"))
        repair_grace = float(os.getenv("REPAIR_DOWN_GRACE_SECONDS", "60"))
        repair_concurrency = int(os.getenv("REPAIR_MAX_CONCURRENT_JOBS", "2"))
        repair_timeout = float(os.getenv("REPAIR_TRANSFER_TIMEOUT_SECONDS", "120"))
        repair_backoff = float(os.getenv("REPAIR_RETRY_BACKOFF_SECONDS", "30"))
    except ValueError as error:
        raise ValueError("replica repair settings must be numeric") from error
    try:
        reconciliation_interval = float(
            os.getenv("RECONCILIATION_INTERVAL_SECONDS", "15")
        )
        reconciliation_backoff = float(
            os.getenv("RECONCILIATION_RETRY_BACKOFF_SECONDS", "30")
        )
        reconciliation_attempts = int(
            os.getenv("RECONCILIATION_MAX_ATTEMPTS", "10")
        )
        reconciliation_concurrency = int(
            os.getenv("RECONCILIATION_MAX_CONCURRENT_JOBS", "2")
        )
        reconciliation_timeout = float(
            os.getenv("RECONCILIATION_TRANSFER_TIMEOUT_SECONDS", "120")
        )
    except ValueError as error:
        raise ValueError("upload reconciliation settings must be numeric") from error

    def boolean_setting(name: str, default: str) -> bool:
        value = os.getenv(name, default).strip().lower()
        if value not in {"true", "false"}:
            raise ValueError(f"{name} must be true or false")
        return value == "true"

    return CoordinatorSettings(
        nodes=nodes,
        request_timeout_seconds=timeout,
        transfer_timeout_seconds=transfer_timeout,
        max_upload_size_bytes=int(max_upload_mb * 1024 * 1024),
        replication_factor=replication_factor,
        health_check_interval_seconds=health_interval,
        health_check_timeout_seconds=health_timeout,
        health_failure_threshold=failure_threshold,
        health_recovery_threshold=recovery_threshold,
        repair_enabled=boolean_setting("REPAIR_ENABLED", "true"),
        repair_interval_seconds=repair_interval,
        repair_down_grace_seconds=repair_grace,
        repair_max_concurrent_jobs=repair_concurrency,
        repair_transfer_timeout_seconds=repair_timeout,
        repair_retry_backoff_seconds=repair_backoff,
        repair_excess_cleanup_enabled=boolean_setting(
            "REPAIR_EXCESS_CLEANUP_ENABLED", "true"
        ),
        repair_internal_token=os.getenv(
            "REPAIR_INTERNAL_TOKEN", "!development-repair-token!"
        ),
        reconciliation_enabled=boolean_setting(
            "RECONCILIATION_ENABLED", "true"
        ),
        reconciliation_interval_seconds=reconciliation_interval,
        reconciliation_retry_backoff_seconds=reconciliation_backoff,
        reconciliation_max_attempts=reconciliation_attempts,
        reconciliation_max_concurrent_jobs=reconciliation_concurrency,
        reconciliation_transfer_timeout_seconds=reconciliation_timeout,
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
