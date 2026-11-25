"""Static checks for the two-host Docker Compose contract."""

from pathlib import Path


COMPOSE_FILE = (
    Path(__file__).resolve().parents[2] / "infrastructure" / "docker" / "compose.yaml"
)
COMPOSE_TEXT = COMPOSE_FILE.read_text(encoding="utf-8")
RF3_TEST_COMPOSE_TEXT = (
    COMPOSE_FILE.with_name("compose.rf3-test.yaml").read_text(encoding="utf-8")
)


def test_compose_declares_independent_profiles() -> None:
    assert "profiles:\n      - coordinator" in COMPOSE_TEXT
    assert "profiles:\n    - storage" in COMPOSE_TEXT
    assert "depends_on:" not in COMPOSE_TEXT
    assert "profiles:\n      - monitor" in COMPOSE_TEXT
    assert "profiles:\n      - repair" in COMPOSE_TEXT
    assert "profiles:\n      - reconciliation" in COMPOSE_TEXT


def test_compose_publishes_exact_storage_ports() -> None:
    for external_port in range(8001, 8007):
        assert f'- "{external_port}:8000"' in COMPOSE_TEXT


def test_compose_passes_configurable_urls_to_coordinator() -> None:
    node_names = ("BIO", "PHY", "MATH", "CHEM", "EARTH", "AGRO")
    for node_name in node_names:
        assert f"NODE_{node_name}_URL: ${{NODE_{node_name}_URL:-" in COMPOSE_TEXT
    assert "NODE_REQUEST_TIMEOUT_SECONDS: ${NODE_REQUEST_TIMEOUT_SECONDS:-2.0}" in (
        COMPOSE_TEXT
    )
    assert "DOCUMENT_TRANSFER_TIMEOUT_SECONDS: ${DOCUMENT_TRANSFER_TIMEOUT_SECONDS:-30.0}" in COMPOSE_TEXT
    assert "MAX_UPLOAD_SIZE_MB: ${MAX_UPLOAD_SIZE_MB:-10}" in COMPOSE_TEXT
    assert "REPLICATION_FACTOR: ${REPLICATION_FACTOR:-3}" in COMPOSE_TEXT
    assert "DATA_ROOT: /data" in COMPOSE_TEXT
    assert "HEALTH_CHECK_INTERVAL_SECONDS: ${HEALTH_CHECK_INTERVAL_SECONDS:-5}" in COMPOSE_TEXT
    assert "HEALTH_CHECK_TIMEOUT_SECONDS: ${HEALTH_CHECK_TIMEOUT_SECONDS:-2}" in COMPOSE_TEXT
    assert "HEALTH_FAILURE_THRESHOLD: ${HEALTH_FAILURE_THRESHOLD:-3}" in COMPOSE_TEXT
    assert "HEALTH_RECOVERY_THRESHOLD: ${HEALTH_RECOVERY_THRESHOLD:-2}" in COMPOSE_TEXT
    assert "REPAIR_DOWN_GRACE_SECONDS: ${REPAIR_DOWN_GRACE_SECONDS:-60}" in COMPOSE_TEXT
    assert "REPAIR_MAX_CONCURRENT_JOBS: ${REPAIR_MAX_CONCURRENT_JOBS:-2}" in COMPOSE_TEXT
    assert "RECONCILIATION_MAX_ATTEMPTS: ${RECONCILIATION_MAX_ATTEMPTS:-10}" in COMPOSE_TEXT
    assert "RECONCILIATION_MAX_CONCURRENT_JOBS: ${RECONCILIATION_MAX_CONCURRENT_JOBS:-2}" in COMPOSE_TEXT
    assert "AUTH_JWT_SECRET: ${AUTH_JWT_SECRET:-" in COMPOSE_TEXT
    assert "AUTH_ACCESS_TOKEN_EXPIRE_MINUTES: ${AUTH_ACCESS_TOKEN_EXPIRE_MINUTES:-30}" in COMPOSE_TEXT
    assert "CORS_ALLOWED_ORIGINS: ${CORS_ALLOWED_ORIGINS:-http://localhost:3000}" in COMPOSE_TEXT
    assert "STORAGE_INTERNAL_TOKEN: ${STORAGE_INTERNAL_TOKEN:-" in COMPOSE_TEXT


def test_rf3_compose_can_exercise_rf1_without_editing_the_file() -> None:
    assert (
        "REPLICATION_FACTOR: ${RF3_TEST_REPLICATION_FACTOR:-3}"
        in RF3_TEST_COMPOSE_TEXT
    )
