from app._version import __version__
from app.template_utils import templates


def test_template_version_matches_app_version():
    assert templates.env.globals["version"] == __version__


def test_version_is_1_2_2():
    assert __version__ == "1.2.2"


def test_footer_shows_version(auth_client):
    resp = auth_client.get("/people")
    assert f"PointBook v{__version__}" in resp.text
