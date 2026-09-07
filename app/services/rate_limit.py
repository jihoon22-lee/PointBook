"""단일 프로세스용 로그인 제한: 동시 접근·메모리 상한·만료를 처리한다.

실제 client IP만 사용한다. Uvicorn 프록시 신뢰는 운영 실행 설정에서 제한한다.
서버 재시작 시 초기화되며 다중 worker 환경을 위한 공유 제한기는 아니다.
"""

import threading
import time
from collections import OrderedDict


class LoginRateLimiter:
    def __init__(
        self, max_attempts: int = 5, window_seconds: int = 300, max_keys: int = 10000
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._failures: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def _keys(self, username: str, ip: str) -> tuple[tuple[str, str], tuple[str, str]]:
        return ((username.strip().lower()[:100], ip), ("", ip))

    def _prune(self, now: float) -> None:
        for key in list(self._failures):
            attempts = [t for t in self._failures[key] if now - t < self.window_seconds]
            if attempts:
                self._failures[key] = attempts
            else:
                del self._failures[key]

    def locked_for(self, username: str, ip: str) -> int:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            remaining = 0
            for key in self._keys(username, ip):
                attempts = self._failures.get(key, [])
                if len(attempts) >= self.max_attempts:
                    remaining = max(
                        remaining, max(1, int(self.window_seconds - (now - attempts[0])))
                    )
            return remaining

    def record_failure(self, username: str, ip: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            for key in set(self._keys(username, ip)):
                attempts = self._failures.setdefault(key, [])
                if len(attempts) < self.max_attempts:
                    attempts.append(now)
                self._failures.move_to_end(key)
            while len(self._failures) > self.max_keys:
                self._failures.popitem(last=False)

    def reset(self, username: str, ip: str) -> None:
        with self._lock:
            for key in self._keys(username, ip):
                self._failures.pop(key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._failures.clear()


login_limiter = LoginRateLimiter()
