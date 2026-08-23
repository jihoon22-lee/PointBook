from pathlib import Path

import yaml


def test_production_compose_preserves_sqlite_and_is_session_independent():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    app = compose["services"]["app"]

    assert app["restart"] == "unless-stopped"
    assert app["init"] is True
    assert "127.0.0.1:${POINTBOOK_PORT:-8002}:8000" in app["ports"]
    assert "${POINTBOOK_DATA_DIR:-./data}:/app/data" in app["volumes"]
    assert app["environment"]["DATABASE_PATH"] == "/app/data/pointbook.db"
    assert app["env_file"][0]["path"] == "${POINTBOOK_ENV_FILE:-.env}"
    assert "healthcheck" in app


def test_production_image_is_reproducible_and_runs_as_non_root():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "uv sync --frozen --no-dev" in dockerfile
    assert "USER pointbook" in dockerfile
    assert "python -m scripts.init_db" in dockerfile
    assert "exec uvicorn app.main:app" in dockerfile


def test_docker_context_excludes_secrets_and_real_data():
    ignored = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in ignored
    assert "data/" in ignored


def test_production_smoke_uses_isolated_project_data_and_cleanup():
    script = Path("e2e/production-smoke.sh").read_text(encoding="utf-8")

    assert "mktemp -d" in script
    assert "COMPOSE_PROJECT_NAME" in script
    assert "POINTBOOK_DATA_DIR" in script
    assert "POINTBOOK_PORT" in script
    assert "docker compose down" in script
    assert "docker compose restart app" in script
    assert "pointbook_smoke_marker" in script
