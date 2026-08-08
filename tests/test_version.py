import tomllib
from pathlib import Path

from app.template_utils import templates


def test_template_version_matches_pyproject():
    pyproject = tomllib.loads(
        (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert templates.env.globals["version"] == pyproject["project"]["version"]


def test_version_is_1_0_0():
    assert templates.env.globals["version"] == "1.0.0"


def test_footer_shows_version(auth_client):
    resp = auth_client.get("/people")
    assert "PointBook v1.0.0" in resp.text
