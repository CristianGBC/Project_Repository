"""Static checks for the two-host Docker Compose contract."""

from pathlib import Path


COMPOSE_FILE = (
    Path(__file__).resolve().parents[2] / "infrastructure" / "docker" / "compose.yaml"
)
COMPOSE_TEXT = COMPOSE_FILE.read_text(encoding="utf-8")


def test_compose_declares_independent_profiles() -> None:
    assert "profiles:\n      - coordinator" in COMPOSE_TEXT
    assert "profiles:\n    - storage" in COMPOSE_TEXT
    assert "depends_on:" not in COMPOSE_TEXT


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
