def test_html_response_no_store(auth_client):
    resp = auth_client.get("/people")
    assert resp.headers.get("cache-control") == "no-store"


def test_static_response_not_no_store(auth_client):
    resp = auth_client.get("/static/css/style.css")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") is None
