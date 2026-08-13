"""로그인 시도 레이트리밋.

단일 관리자 계정에 대한 브루트포스 공격을 막기 위한 인메모리 제한.
단일 프로세스·단일 관리자 환경이라 프로세스 메모리로 충분하다.
"""

import time
from collections import defaultdict


class LoginRateLimiter:
    def __init__(self, max_attempts: int = 5, window_seconds: int = 300) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._failures: dict[tuple[str, str], list[float]] = defaultdict(list)

    def _key(self, username: str, ip: str) -> tuple[str, str]:
        return (username.strip().lower(), ip)

    def _prune(self, key: tuple[str, str], now: float) -> None:
        self._failures[key] = [t for t in self._failures[key] if now - t < self.window_seconds]

    def locked_for(self, username: str, ip: str) -> int:
        """잠금이면 남은 시간(초)을, 아니면 0을 반환한다."""
        key = self._key(username, ip)
        now = time.monotonic()
        self._prune(key, now)
        attempts = self._failures[key]
        if len(attempts) >= self.max_attempts:
            return max(1, int(self.window_seconds - (now - attempts[0])))
        return 0

    def record_failure(self, username: str, ip: str) -> None:
        key = self._key(username, ip)
        now = time.monotonic()
        self._failures[key].append(now)
        self._prune(key, now)

    def reset(self, username: str, ip: str) -> None:
        self._failures.pop(self._key(username, ip), None)

    def clear_all(self) -> None:
        self._failures.clear()


login_limiter = LoginRateLimiter()
