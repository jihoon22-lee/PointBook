from app._version import __version__
from app.template_utils import templates


def test_template_version_matches_app_version():
    assert templates.env.globals["version"] == __version__


def test_version_is_1_4_4():
    assert __version__ == "1.4.4"


def test_footer_shows_version(auth_client):
    resp = auth_client.get("/people")
    assert f"PointBook v{__version__}" in resp.text
