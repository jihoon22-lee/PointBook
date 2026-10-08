"""초안의 문서 대조 안내와 펼쳐 볼 원문. 이전 초안의 구분자도 읽는다."""

EVIDENCE_MARKER = "\n사진 대조 원문: "
LEGACY_MARKER = "\n미사용 명단 원문: "


def split_notice(notice: str) -> tuple[str, str]:
    marker = EVIDENCE_MARKER if EVIDENCE_MARKER in notice else LEGACY_MARKER
    summary, _separator, evidence = notice.partition(marker)
    return summary, evidence


def join_notices(*notices: str) -> str:
    parts = [split_notice(notice) for notice in notices if notice]
    summary = "\n".join(text for text, _evidence in parts if text)
    evidence = "\n".join(text for _summary, text in parts if text)
    return summary + (EVIDENCE_MARKER + evidence if evidence else "")
