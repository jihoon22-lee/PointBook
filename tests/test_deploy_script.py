from pathlib import Path


def test_deploy_stops_server_before_database_backup():
    script = Path("scripts/deploy.sh").read_text(encoding="utf-8")

    stop_position = script.index("scripts/stop.sh")
    backup_position = script.index("cp data/pointbook.db")
    start_position = script.index("scripts/run.sh")

    assert stop_position < backup_position < start_position
    assert "scripts/stop.sh || true" not in script


def test_stop_script_waits_for_process_exit():
    script = Path("scripts/stop.sh").read_text(encoding="utf-8")

    assert "for _ in $(seq" in script
    assert "kill -0" in script
    assert "종료를 확인하지 못했습니다" in script
