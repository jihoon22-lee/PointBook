import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def shell_environment(tmp_path):
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    for script in Path("scripts").glob("*.sh"):
        shutil.copy(script, root / "scripts" / script.name)
    (root / "docker-compose.yml").write_text(Path("docker-compose.yml").read_text())
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s | image=%s\\n' "$*" "${POINTBOOK_IMAGE:-}" >> "$EVENTS"
if [ "$1" = compose ]; then
  shift
  while [[ "$1" = --env-file || "$1" = -f ]]; do shift 2; done
  case "$1" in
    config)
      python3 -c 'import json,os; print(json.dumps({"name":os.environ.get("COMPOSE_PROJECT_NAME","pointbook"),"services":{"app":{"image":os.environ.get("POINTBOOK_IMAGE","synthetic-wp03:local"),"volumes":[{"type":"bind","source":os.environ["POINTBOOK_DATA_DIR"],"target":"/app/data"},{"type":"bind","source":os.environ["POINTBOOK_BACKUP_DIR"],"target":"/app/backups"}],"ports":[{"published":"18002"}]}}}))'
      ;;
    build) [ "${FAIL_STAGE:-}" != build ] ;;
    ps) if [ "${NO_CONTAINER:-}" != yes ]; then echo synthetic-container; fi ;;
    run)
      if [ "${REQUIRE_NEW_HELPER:-}" = yes ]; then [ "${POINTBOOK_HANDOFF_HELPER_LOADED:-}" = yes ] || exit 91; fi
      if [[ "$*" = *scripts.preflight* ]]; then [ "${FAIL_STAGE:-}" != rehearsal ] || exit 1; fi
      if [[ "$*" = *validate_runtime* ]]; then [ "${FAIL_STAGE:-}" != runtime ] || exit 1; fi
      if [[ "$*" = *scripts.backup* ]]; then [ "${FAIL_STAGE:-}" != backup ] || exit 1; echo /app/backups/synthetic.db;
      elif [[ "$*" = *scripts.restore* && "$*" != *--apply* ]]; then [ "${FAIL_STAGE:-}" != restore_verify ]; fi
      ;;
  esac
elif [ "$1" = inspect ]; then
  if [[ "$*" = *Health.Status* ]]; then
    if [ "${FAIL_STAGE:-}" = health ]; then echo unhealthy; else echo healthy; fi
  elif [[ "$*" = *State.Running* ]]; then echo true;
  elif [ "${FAIL_STAGE:-}" = image_mismatch ]; then echo "$OLD_IMAGE_ID";
  else echo "${POINTBOOK_IMAGE:-$OLD_IMAGE_ID}"; fi
elif [ "$1" = image ] && [ "$2" = inspect ]; then
  reference="${@: -1}"
  if [[ "$reference" = sha256:* ]]; then echo "$reference"; else echo "$BUILD_IMAGE_ID"; fi
fi
"""
    )
    docker.chmod(0o755)
    for name, content in {
        "git": '#!/bin/sh\nprintf "git %s\\n" "$*" >> "$EVENTS"\nif [ "$1" = rev-parse ]; then echo synthetic-sha; fi\nif [ "$1" = checkout ]; then printf "\\nexport POINTBOOK_HANDOFF_HELPER_LOADED=yes\\n" >> scripts/compose-common.sh; fi\n',
        "curl": "#!/bin/sh\nexit 0\n",
    }.items():
        path = binary / name
        path.write_text(content)
        path.chmod(0o755)
    env_file = tmp_path / "custom.env"
    env_file.write_text("ADMIN_USERNAME=synthetic-user\n")
    environment = os.environ.copy() | {
        "PATH": f"{binary}:{os.environ['PATH']}",
        "POINTBOOK_DATA_DIR": str(tmp_path / "custom data"),
        "POINTBOOK_BACKUP_DIR": str(tmp_path / "protected backups"),
        "POINTBOOK_ENV_FILE": str(env_file),
        "COMPOSE_PROJECT_NAME": "synthetic-wp03",
        "EVENTS": str(tmp_path / "events"),
        "OLD_IMAGE_ID": "sha256:" + "1" * 64,
        "BUILD_IMAGE_ID": "sha256:" + "2" * 64,
    }
    return root, environment


def execute(shell_environment, script, *arguments, failure=""):
    root, environment = shell_environment
    result = subprocess.run(
        ["bash", f"scripts/{script}", *arguments],
        cwd=root,
        env=environment | {"FAIL_STAGE": failure},
        text=True,
        capture_output=True,
        check=False,
    )
    return result, Path(environment["EVENTS"]).read_text().splitlines()


def test_deploy_preserves_image_builds_stops_backs_up_and_verifies(shell_environment):
    _, env = shell_environment
    directory = Path(env["POINTBOOK_DATA_DIR"])
    directory.mkdir()
    (directory / "pointbook.db").touch()
    result, events = execute(shell_environment, "deploy.sh")
    assert result.returncode == 0, result.stderr
    index = lambda text: next(i for i, event in enumerate(events) if text in event)
    assert index("image tag") < index("build app") < index("stop app")
    assert index("stop app") < index("scripts.backup") < index("up -d --no-build --pull never app")
    assert (
        index("up -d --no-build --pull never app")
        < index("restart app")
        < index("--verify-current")
    )
    assert (directory / "previous-image.txt").read_text().startswith(env["OLD_IMAGE_ID"])
    assert (directory / "deployed-source.txt").read_text().strip() == "synthetic-sha"
    assert (directory / "deployed-image.txt").read_text().strip() == env["BUILD_IMAGE_ID"]
    assert index("git checkout") < index("config --format json")
    assert all(
        event.endswith("image=" + env["BUILD_IMAGE_ID"])
        for event in events
        if " run " in event or " up " in event
    )
    assert all(
        env["POINTBOOK_ENV_FILE"] in event for event in events if event.startswith("compose")
    )


@pytest.mark.parametrize(
    "failure", ["build", "backup", "health", "runtime", "rehearsal", "image_mismatch"]
)
def test_deploy_failures_do_not_automatically_restore_database(shell_environment, failure):
    _, env = shell_environment
    directory = Path(env["POINTBOOK_DATA_DIR"])
    directory.mkdir()
    (directory / "pointbook.db").write_text("synthetic sentinel")
    result, events = execute(shell_environment, "deploy.sh", failure=failure)
    assert result.returncode != 0
    assert all("--apply" not in event for event in events)
    assert (directory / "pointbook.db").read_text() == "synthetic sentinel"
    if failure in {"build", "runtime", "rehearsal"}:
        assert all("stop app" not in event for event in events)
    if failure == "backup":
        assert all(" up " not in event for event in events)


def test_run_creates_custom_directories_and_stop_only_targets_app(shell_environment):
    _, env = shell_environment
    result, _ = execute(shell_environment, "run.sh")
    assert result.returncode == 0, result.stderr
    assert Path(env["POINTBOOK_DATA_DIR"]).is_dir()
    assert Path(env["POINTBOOK_BACKUP_DIR"]).is_dir()
    result, events = execute(shell_environment, "stop.sh")
    assert result.returncode == 0
    assert "stop app" in events[-1]
    assert all("docker stop" not in event and "kill" not in event for event in events)


def test_restore_verifies_before_stop_and_restarts_after_apply(shell_environment, tmp_path):
    backup = tmp_path / "synthetic.db"
    backup.touch()
    result, events = execute(shell_environment, "restore.sh", str(backup))
    assert result.returncode == 0, result.stderr
    verify = next(i for i, event in enumerate(events) if "scripts.restore" in event)
    stop = next(i for i, event in enumerate(events) if "stop app" in event)
    apply = next(i for i, event in enumerate(events) if "--service-stopped" in event)
    assert verify < stop < apply
    assert any("restart app" in event for event in events)


def test_corrupt_restore_does_not_stop_app(shell_environment, tmp_path):
    backup = tmp_path / "synthetic.db"
    backup.touch()
    result, events = execute(shell_environment, "restore.sh", str(backup), failure="restore_verify")
    assert result.returncode != 0
    assert all("stop app" not in event for event in events)


def test_schedule_rejects_overlapping_maintenance(shell_environment):
    import fcntl

    _, env = shell_environment
    directory = Path(env["POINTBOOK_DATA_DIR"])
    directory.mkdir()
    Path(env["EVENTS"]).touch()
    with (directory / ".maintenance.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result, events = execute(shell_environment, "scheduled-backup.sh")
    assert result.returncode != 0
    assert "다른 백업/배포/복원" in result.stderr
    assert all("config --format json" in event for event in events)


def test_deploy_explicit_verified_image_is_not_rebuilt(shell_environment):
    _, env = shell_environment
    selected = "sha256:" + "3" * 64
    env["POINTBOOK_DEPLOY_IMAGE"] = selected
    result, events = execute(shell_environment, "deploy.sh")
    assert result.returncode == 0, result.stderr
    assert all("build app" not in event for event in events)
    assert all(
        event.endswith("image=" + selected)
        for event in events
        if " run " in event or " up " in event
    )
    assert (Path(env["POINTBOOK_DATA_DIR"]) / "deployed-image.txt").read_text().strip() == selected


@pytest.mark.parametrize("script", ["scheduled-backup.sh", "restore.sh"])
def test_recovery_pins_running_image_even_if_mutable_config_tag_changes(
    shell_environment, tmp_path, script
):
    _, env = shell_environment
    env["POINTBOOK_DEPLOY_IMAGE"] = "sha256:" + "3" * 64
    backup = tmp_path / "synthetic.db"
    backup.touch()
    arguments = (str(backup),) if script == "restore.sh" else ()
    result, events = execute(shell_environment, script, *arguments)
    assert result.returncode == 0, result.stderr
    runs = [event for event in events if " run " in event or " up " in event]
    assert runs and all(event.endswith("image=" + env["OLD_IMAGE_ID"]) for event in runs)
    assert all("build app" not in event for event in events)


def test_schedule_without_container_pins_explicit_image(shell_environment):
    _, env = shell_environment
    selected = "sha256:" + "3" * 64
    env |= {"POINTBOOK_DEPLOY_IMAGE": selected, "NO_CONTAINER": "yes"}
    result, events = execute(shell_environment, "scheduled-backup.sh")
    assert result.returncode == 0, result.stderr
    assert all(event.endswith("image=" + selected) for event in events if " run " in event)


def test_deploy_exec_handoff_loads_new_helper_before_any_app_operation(shell_environment):
    _, env = shell_environment
    env["REQUIRE_NEW_HELPER"] = "yes"
    result, _ = execute(shell_environment, "deploy.sh")
    assert result.returncode == 0, result.stderr


def test_run_existing_database_backs_up_before_start_even_for_same_image(shell_environment):
    _, env = shell_environment
    directory = Path(env["POINTBOOK_DATA_DIR"])
    directory.mkdir()
    (directory / "pointbook.db").touch()
    result, events = execute(shell_environment, "run.sh")
    assert result.returncode == 0, result.stderr
    position = lambda name: next(i for i, event in enumerate(events) if name in event)
    assert position("scripts.preflight") < position("stop app") < position("scripts.backup")
    assert position("scripts.backup") < position("up -d") < position("--verify-current")


def test_run_failed_rehearsal_keeps_existing_service_running(shell_environment):
    _, env = shell_environment
    directory = Path(env["POINTBOOK_DATA_DIR"])
    directory.mkdir()
    (directory / "pointbook.db").touch()
    result, events = execute(shell_environment, "run.sh", failure="rehearsal")
    assert result.returncode != 0
    assert all("stop app" not in event for event in events)
