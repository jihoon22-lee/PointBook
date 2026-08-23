import os
import subprocess
from pathlib import Path


def test_deploy_builds_before_stop_and_backs_up_before_compose_start():
    script = Path("scripts/deploy.sh").read_text(encoding="utf-8")

    build_position = script.index("docker compose build app")
    stop_position = script.index("scripts/stop.sh")
    backup_position = script.index("cp data/pointbook.db")
    start_position = script.index("docker compose up -d app")

    assert build_position < stop_position < backup_position < start_position
    assert "scripts/stop.sh || true" not in script


def test_run_starts_compose_and_waits_for_health():
    script = Path("scripts/run.sh").read_text(encoding="utf-8")

    assert "docker compose up -d --build app" in script
    assert "Health.Status" in script
    assert '"unhealthy"' in script
    assert "docker compose logs" in script


def test_run_creates_missing_host_data_directory_before_compose(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = "compose" ] && [ "$2" = "up" ]; then
  test -d "$POINTBOOK_DATA_DIR" || exit 91
elif [ "$1" = "compose" ] && [ "$2" = "ps" ]; then
  echo fake-container
elif [ "$1" = "inspect" ]; then
  echo healthy
fi
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    data_dir = tmp_path / "fresh-data"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "POINTBOOK_DATA_DIR": str(data_dir),
    }

    result = subprocess.run(
        ["bash", "scripts/run.sh"],
        cwd=Path.cwd(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert data_dir.is_dir()


def test_stop_targets_only_pointbook_compose_and_owned_legacy_pid():
    script = Path("scripts/stop.sh").read_text(encoding="utf-8")

    assert "docker compose stop app" in script
    assert "data/server.pid" in script
    assert "for _ in $(seq" in script
    assert "kill -0" in script
    assert "/proc/$PID/cwd" in script
    assert "uvicorn app.main:app" in script
    assert "종료를 확인하지 못했습니다" in script
    assert "docker stop" not in script


def test_deploy_verifies_compose_health_and_host_login():
    script = Path("scripts/deploy.sh").read_text(encoding="utf-8")

    assert "Health.Status" in script
    assert "http://127.0.0.1:${POINTBOOK_PORT}/login" in script
    assert "curl" in script
