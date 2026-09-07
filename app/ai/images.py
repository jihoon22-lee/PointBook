"""디코딩/변환 없이 이미지 컨테이너 헤더와 확장자를 대조한다.

HEIC는 Gemini가 공식 지원하는 image/heic로 원본을 전달한다. 로컬 픽셀 디코더가
아니므로 헤더 검사 통과를 실제 제공자의 디코딩 성공으로 주장하지 않는다.
"""

from pathlib import PurePath

from app.ai.base import VisionError

MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
}


def image_mime(data: bytes, filename: str) -> str:
    expected = MIME_BY_EXT.get(PurePath(filename.lower()).suffix)
    actual = ""
    if (
        len(data) >= 45
        and data.startswith(b"\x89PNG\r\n\x1a\n")
        and data[8:16] == b"\x00\x00\x00\rIHDR"
        and data[-8:] == b"IEND\xaeB`\x82"
        and int.from_bytes(data[16:20], "big") > 0
        and int.from_bytes(data[20:24], "big") > 0
    ):
        actual = "image/png"
    elif len(data) >= 12 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        actual = "image/jpeg"
    elif (
        len(data) >= 20
        and data[:4] == b"RIFF"
        and data[8:12] == b"WEBP"
        and data[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
        and int.from_bytes(data[4:8], "little") + 8 == len(data)
    ):
        actual = "image/webp"
    elif len(data) >= 24 and data[4:8] == b"ftyp":
        box_size = int.from_bytes(data[:4], "big")
        if 16 <= box_size <= len(data) and box_size % 4 == 0:
            brands = [data[8:12]] + [data[i : i + 4] for i in range(16, box_size, 4)]
            if any(brand in {b"heic", b"heix", b"hevc", b"hevx"} for brand in brands):
                actual = "image/heic"
    if not expected or actual != expected:
        raise VisionError(
            "이미지 확장자와 파일 형식이 일치하지 않거나 파일이 손상되었습니다.",
            code="image_format",
        )
    return actual
