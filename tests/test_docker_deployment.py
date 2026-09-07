from pathlib import Path

import yaml


def test_production_compose_preserves_sqlite_and_is_session_independent():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    app = compose["services"]["app"]

    assert "${COMPOSE_PROJECT_NAME:-pointbook}" in app["image"]
    assert "${POINTBOOK_IMAGE:-" in app["image"]
    assert app["restart"] == "unless-stopped"
    assert app["init"] is True
    assert app["user"] == "${POINTBOOK_UID:-1000}:${POINTBOOK_GID:-1000}"
    assert "127.0.0.1:${POINTBOOK_PORT:-8002}:8000" in app["ports"]
    assert "${POINTBOOK_DATA_DIR:-./data}:/app/data" in app["volumes"]
    assert app["environment"]["DATABASE_PATH"] == "/app/data/pointbook.db"
    assert app["env_file"][0]["path"] == "${POINTBOOK_ENV_FILE:-.env}"
    assert "ADMIN_PASSWORD" not in app["environment"]
    assert "SECRET_KEY" not in app["environment"]
    assert "/health" in app["healthcheck"]["test"][-1]
    assert (
        "${POINTBOOK_BACKUP_DIR:-${POINTBOOK_DATA_DIR:-./data}/backups}:/app/backups"
        in app["volumes"]
    )


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
    assert 'export POINTBOOK_UID="$(id -u)"' in script
    assert 'export POINTBOOK_GID="$(id -g)"' in script
    assert "pb_compose down" in script
    assert "pb_compose restart app" in script
    assert "pointbook_smoke_marker" in script


def test_compose_custom_env_preserves_credentials_and_resolves_data_backup_paths(tmp_path):
    import json
    import os
    import shutil
    import subprocess

    import pytest

    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI unavailable")
    env_file = tmp_path / "custom.env"
    data = tmp_path / "custom-data"
    env_file.write_text(
        "ADMIN_USERNAME=custom-synthetic\nADMIN_PASSWORD=custom-synthetic-password\n"
        "SECRET_KEY=custom-synthetic-secret\nAI_PROVIDER=gemini\n"
        f"POINTBOOK_DATA_DIR={data}\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("POINTBOOK_")
        and key not in {"ADMIN_USERNAME", "ADMIN_PASSWORD", "SECRET_KEY", "AI_PROVIDER"}
    }
    environment["POINTBOOK_ENV_FILE"] = str(env_file)
    environment["COMPOSE_PROJECT_NAME"] = "synthetic-custom"
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "config", "--format", "json"],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    app = json.loads(result.stdout)["services"]["app"]
    assert app["environment"]["ADMIN_USERNAME"] == "custom-synthetic"
    assert app["environment"]["ADMIN_PASSWORD"] == "custom-synthetic-password"
    assert app["environment"]["AI_PROVIDER"] == "gemini"
    mounts = {v["target"]: v["source"] for v in app["volumes"]}
    assert mounts["/app/data"] == str(data)
    assert mounts["/app/backups"] == str(data / "backups")
    assert app["image"].startswith("synthetic-custom:")
