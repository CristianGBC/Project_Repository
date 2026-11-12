"""Institutional configuration tests."""

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
