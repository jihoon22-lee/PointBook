"""애플리케이션 로깅 설정.

시작·마이그레이션·확정·백업·보안 경고 등 운영에 필요한 이벤트를 남긴다.
민감정보(API 키, 비밀번호)는 절대 로그에 남기지 않는다.
"""

import logging

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _configured = True


def get_logger(name: str = "pointbook") -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def log_security_warnings(warnings: list[str]) -> None:
    logger = get_logger("pointbook.security")
    for message in warnings:
        logger.warning(message)
