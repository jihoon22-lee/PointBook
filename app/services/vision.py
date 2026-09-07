"""한 프로세스의 AI 실행을 제한한다. 취소/시간초과도 실행 중 슬롯을 해제하지 않는다."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from app.ai.base import VisionError, VisionProvider
from app.ai.factory import get_provider
from app.ai.images import image_mime
from app.config import get_settings
from app.services.parsing import RawRequestRow


class VisionExecutor:
    def __init__(self, max_workers: int) -> None:
        self._state_lock = threading.Lock()
        self._active = 0
        self._closed = False
        self._slots = threading.BoundedSemaphore(max_workers)
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pointbook-ai")

    async def extract(
        self, provider: VisionProvider, data: bytes, filename: str, timeout: float
    ) -> list[RawRequestRow]:
        if not self._slots.acquire(blocking=False):
            raise VisionError(
                "다른 AI 인식이 진행 중입니다. 잠시 후 다시 시도해 주세요.", code="busy"
            )
        with self._state_lock:
            self._active += 1
        try:
            future = self._pool.submit(provider.extract_table, data, filename)
        except RuntimeError:
            self._release()
            raise VisionError(
                "AI 실행이 종료 중입니다. 잠시 후 다시 시도해 주세요.", code="shutdown"
            ) from None
        future.add_done_callback(lambda _: self._release())
        wrapped = asyncio.wrap_future(future)
        # Consume eventual failures even if HTTP client disconnected or timed out.
        wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=timeout)
        except TimeoutError:
            raise VisionError(
                "AI 인식 시간이 초과되었습니다. 기존 인식 종료 후 재시도해 주세요.", code="timeout"
            ) from None
        except VisionError:
            raise
        except Exception:  # noqa: BLE001 — 외부 예외에는 키·원문이 포함될 수 있다.
            raise VisionError("AI 인식에 실패했습니다.", code="worker_failure") from None

    def _release(self) -> None:
        with self._state_lock:
            self._active -= 1
        self._slots.release()

    @property
    def closed_and_idle(self) -> bool:
        with self._state_lock:
            return self._closed and self._active == 0

    def shutdown(self) -> None:
        with self._state_lock:
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)


_executor: VisionExecutor | None = None
_executor_lock = threading.Lock()


def shutdown_vision() -> None:
    global _executor
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown()
            if _executor.closed_and_idle:
                _executor = None


async def extract_image(data: bytes, filename: str) -> list[RawRequestRow]:
    settings = get_settings()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise VisionError("이미지 크기가 허용 한도를 초과했습니다.", code="image_limit")
    image_mime(data, filename)
    provider = get_provider()
    global _executor
    with _executor_lock:
        if _executor is None or _executor.closed_and_idle:
            _executor = VisionExecutor(settings.ai_max_concurrency)
        executor = _executor
    return await executor.extract(provider, data, filename, settings.ai_timeout_seconds)
