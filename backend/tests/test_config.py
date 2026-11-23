"""Institutional configuration tests."""

import pytest

from backend.app.config import NODE_IDENTITIES, load_settings


EXPECTED_IDENTITIES = [
    ("node-bio", "Escuela de Ciencias Biológicas e Ingeniería"),
    ("node-phy", "Escuela de Ciencias Físicas y Nanotecnología"),
    ("node-math", "Escuela de Ciencias Matemáticas y Computacionales"),
    ("node-chem", "Escuela de Ciencias Químicas e Ingeniería"),
    ("node-earth", "Escuela de Ciencias de la Tierra, Energía y Ambiente"),
    ("node-agro", "Escuela de Ciencias Agropecuarias y Agroindustriales"),
]


def test_six_node_ids_match_their_schools() -> None:
    configured = [(node_id, school) for node_id, school, _ in NODE_IDENTITIES]
    assert configured == EXPECTED_IDENTITIES


def test_node_url_can_be_changed_with_environment(monkeypatch) -> None:
    monkeypatch.setenv("NODE_MATH_URL", "http://192.168.10.20:8001/")

    settings = load_settings()
    math_node = next(node for node in settings.nodes if node.node_id == "node-math")

    assert math_node.url == "http://192.168.10.20:8001"


def test_all_node_urls_support_remote_host_configuration(monkeypatch) -> None:
    remote_urls = {
        "NODE_BIO_URL": "http://192.168.1.23:8001",
        "NODE_PHY_URL": "http://192.168.1.23:8002",
        "NODE_MATH_URL": "http://192.168.1.23:8003",
        "NODE_CHEM_URL": "http://192.168.1.23:8004",
        "NODE_EARTH_URL": "http://192.168.1.23:8005",
        "NODE_AGRO_URL": "http://192.168.1.23:8006",
    }
    for variable, value in remote_urls.items():
        monkeypatch.setenv(variable, value)

    settings = load_settings()

    assert [node.url for node in settings.nodes] == list(remote_urls.values())


def test_database_url_is_loaded_only_from_environment(monkeypatch) -> None:
    database_url = "postgresql+psycopg://app:secret@db.example/yachayrepo_test"
    monkeypatch.setenv("DATABASE_URL", database_url)

    assert load_settings().database_url == database_url


def test_database_url_has_no_implicit_default(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert load_settings().database_url is None


def test_replication_factor_defaults_to_three_and_accepts_one(monkeypatch) -> None:
    monkeypatch.delenv("REPLICATION_FACTOR", raising=False)
    assert load_settings().replication_factor == 3

    monkeypatch.setenv("REPLICATION_FACTOR", "1")
    assert load_settings().replication_factor == 1


@pytest.mark.parametrize("value", ["0", "2", "4", "invalid"])
def test_invalid_replication_factor_is_rejected(monkeypatch, value: str) -> None:
    monkeypatch.setenv("REPLICATION_FACTOR", value)

    with pytest.raises(ValueError, match="REPLICATION_FACTOR"):
        load_settings()


def test_health_monitor_defaults_and_overrides(monkeypatch) -> None:
    for variable in (
        "HEALTH_CHECK_INTERVAL_SECONDS",
        "HEALTH_CHECK_TIMEOUT_SECONDS",
        "HEALTH_FAILURE_THRESHOLD",
        "HEALTH_RECOVERY_THRESHOLD",
    ):
        monkeypatch.delenv(variable, raising=False)
    defaults = load_settings()
    assert defaults.health_check_interval_seconds == 5
    assert defaults.health_check_timeout_seconds == 2
    assert defaults.health_failure_threshold == 3
    assert defaults.health_recovery_threshold == 2

    monkeypatch.setenv("HEALTH_CHECK_INTERVAL_SECONDS", "1.5")
    monkeypatch.setenv("HEALTH_CHECK_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("HEALTH_FAILURE_THRESHOLD", "4")
    monkeypatch.setenv("HEALTH_RECOVERY_THRESHOLD", "3")
    configured = load_settings()
    assert configured.health_check_interval_seconds == 1.5
    assert configured.health_check_timeout_seconds == 0.5
    assert configured.health_failure_threshold == 4
    assert configured.health_recovery_threshold == 3


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("HEALTH_CHECK_INTERVAL_SECONDS", "0"),
        ("HEALTH_CHECK_INTERVAL_SECONDS", "invalid"),
        ("HEALTH_CHECK_TIMEOUT_SECONDS", "-1"),
        ("HEALTH_FAILURE_THRESHOLD", "0"),
        ("HEALTH_RECOVERY_THRESHOLD", "0"),
    ],
)
def test_invalid_health_monitor_settings_are_rejected(
    monkeypatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError):
        load_settings()
