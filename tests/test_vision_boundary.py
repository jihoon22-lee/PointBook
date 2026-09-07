import asyncio
import base64
import re
import threading

import httpx
import pytest

from app.ai.base import VisionError
from app.ai.factory import get_provider
from app.ai.images import image_mime
from app.ai.mock import MockProvider
from app.config import Settings
from app.services import vision
from app.services.vision import VisionExecutor

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
)
JPEG = b"\xff\xd8\xff\xe0" + b"synthetic-jpeg" + b"\xff\xd9"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8L\x04\x00\x00\x00data"
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"


@pytest.mark.parametrize(
    ("data", "filename", "mime"),
    [
        (PNG, "req.PNG", "image/png"),
        (JPEG, "req.jpeg", "image/jpeg"),
        (JPEG, "req.jpg", "image/jpeg"),
        (WEBP, "req.webp", "image/webp"),
        (HEIC, "req.heic", "image/heic"),
    ],
)
def test_matching_container_headers(data, filename, mime):
    assert image_mime(data, filename) == mime


@pytest.mark.parametrize(
    ("data", "filename"),
    [
        (b"fake-image", "req.png"),
        (PNG, "req.jpg"),
        (JPEG, "req.png"),
        (PNG[:30], "req.png"),
        (JPEG[:-2], "req.jpg"),
        (WEBP[:-1], "req.webp"),
        (HEIC.replace(b"heic", b"avif"), "req.heic"),
        (PNG, "req.svg"),
        (b"<html>", "req.jpg"),
    ],
)
def test_wrong_or_truncated_headers_rejected(data, filename):
    with pytest.raises(VisionError, match="파일 형식"):
        image_mime(data, filename)


@pytest.mark.parametrize(
    ("name", "mode"),
    [("", "development"), ("typo", "test"), ("openai", "development"), ("mock", "production")],
)
def test_provider_configuration_never_falls_back(monkeypatch, name, mode):
    monkeypatch.setattr(
        "app.ai.factory.get_settings", lambda: Settings(ai_provider=name, app_env=mode)
    )
    with pytest.raises(VisionError, match="제공자 설정"):
        get_provider()


class WaitingProvider(MockProvider):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def extract_table(self, data, filename):
        self.started.set()
        try:
            if not self.release.wait(5):
                raise RuntimeError("test worker not released")
            return super().extract_table(data, filename)
        finally:
            self.finished.set()


def test_timeout_keeps_worker_slot_until_actual_completion():
    executor = VisionExecutor(1)
    provider = WaitingProvider()

    async def scenario():
        try:
            with pytest.raises(VisionError, match="시간이 초과"):
                await executor.extract(provider, PNG, "req.png", 0.02)
            assert provider.started.is_set()
            with pytest.raises(VisionError, match="진행 중"):
                await executor.extract(MockProvider(), PNG, "req.png", 1)
            provider.release.set()
            await asyncio.to_thread(provider.finished.wait, 1)
            await asyncio.sleep(0.01)
            assert await executor.extract(MockProvider(), PNG, "req.png", 1)
        finally:
            provider.release.set()
            executor.shutdown()

    asyncio.run(scenario())


def test_cancelled_client_keeps_worker_slot_and_other_async_work_progresses():
    executor = VisionExecutor(1)
    provider = WaitingProvider()

    async def scenario():
        task = asyncio.create_task(executor.extract(provider, PNG, "req.png", 3))
        try:
            assert await asyncio.to_thread(provider.started.wait, 1)
            await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(VisionError, match="진행 중"):
                await executor.extract(MockProvider(), PNG, "req.png", 1)
        finally:
            provider.release.set()
            await asyncio.to_thread(provider.finished.wait, 1)
            executor.shutdown()

    asyncio.run(scenario())


def test_worker_exception_never_exposes_provider_text(caplog):
    class FailingProvider(MockProvider):
        def extract_table(self, data, filename):
            raise RuntimeError("private-person / https://example.test?key=synthetic-secret")

    async def scenario():
        executor = VisionExecutor(1)
        try:
            with pytest.raises(VisionError) as error:
                await executor.extract(FailingProvider(), PNG, "req.png", 1)
            assert "private-person" not in str(error.value) + caplog.text
            assert "synthetic-secret" not in str(error.value) + caplog.text
        finally:
            executor.shutdown()

    asyncio.run(scenario())


def test_shutdown_is_lazy_restartable_without_exceeding_running_limit(monkeypatch):
    vision.shutdown_vision()
    provider = WaitingProvider()
    monkeypatch.setattr(vision, "get_settings", lambda: Settings(ai_max_concurrency=1))
    monkeypatch.setattr(vision, "get_provider", lambda: provider)

    async def scenario():
        task = asyncio.create_task(vision.extract_image(PNG, "req.png"))
        try:
            assert await asyncio.to_thread(provider.started.wait, 1)
            vision.shutdown_vision()
            with pytest.raises(VisionError, match="진행 중"):
                await vision.extract_image(PNG, "req.png")
            provider.release.set()
            assert await task
            monkeypatch.setattr(vision, "get_provider", MockProvider)
            assert await vision.extract_image(PNG, "req.png")
        finally:
            provider.release.set()
            vision.shutdown_vision()

    asyncio.run(scenario())


def test_image_size_rejected_before_provider(monkeypatch):
    monkeypatch.setattr(vision, "get_settings", lambda: Settings(max_upload_mb=1))
    monkeypatch.setattr(vision, "get_provider", lambda: pytest.fail("provider must not be called"))
    with pytest.raises(VisionError, match="이미지 크기"):
        asyncio.run(vision.extract_image(PNG + b"x" * (1024 * 1024), "req.png"))


def test_slow_upload_does_not_block_health_login_or_people(auth_client, monkeypatch):
    from app.main import app

    provider = WaitingProvider()
    monkeypatch.setattr(vision, "get_provider", lambda: provider)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', auth_client.get("/login").text).group(1)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            cookies=dict(auth_client.cookies),
        ) as client:
            task = asyncio.create_task(
                client.post(
                    "/monthly/upload",
                    headers={"X-CSRF-Token": csrf},
                    data={"month": "2026-07"},
                    files={"file": ("req.png", PNG, "image/png")},
                )
            )
            try:
                assert await asyncio.to_thread(provider.started.wait, 1)
                for path, expected_status in [("/health", 200), ("/login", 303), ("/people", 200)]:
                    response = await asyncio.wait_for(client.get(path), timeout=1)
                    assert response.status_code == expected_status
            finally:
                provider.release.set()
                response = await task
            assert response.status_code == 303
            response = await client.get(response.headers["location"])
            assert response.status_code == 200
            assert "김소방" in response.text

    asyncio.run(scenario())


def test_http_ai_errors_preserve_all_rows_without_database_writes(auth_client, db, monkeypatch):
    import json

    from sqlalchemy import func, select

    from app.models import Person

    rows = [
        {"point_no": "00000011", "personal_no": "101", "name": "합성 정상", "amount": "0"},
        {
            "point_no": "잘못된 번호",
            "personal_no": "102",
            "name": "합성 오류",
            "amount": "5O000",
            "note": "원문 메모",
        },
        None,
    ]
    monkeypatch.setattr(vision, "get_provider", lambda: MockProvider(json.dumps(rows)))
    response = auth_client.post(
        "/monthly/upload", data={"month": "2026-07"}, files={"file": ("req.png", PNG, "image/png")}
    )
    assert response.status_code == 400
    for value in ["합성 정상", "합성 오류", "잘못된 번호", "5O000", "원문 메모", "AI 3행"]:
        assert value in response.text
    assert db.scalar(select(func.count(Person.id))) == 0


def test_http_disguised_image_never_calls_provider(auth_client, monkeypatch):
    monkeypatch.setattr(vision, "get_provider", lambda: pytest.fail("provider must not be called"))
    response = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-07"},
        files={"file": ("req.png", b"not-png", "image/png")},
    )
    assert response.status_code == 400
    assert "파일 형식" in response.text
