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


def _jpeg_end(data: bytes) -> int | None:
    """길이 있는 segment와 entropy scan을 선형 탐색해 주 이미지 EOI를 찾는다.

    APP/COM 안의 EXIF thumbnail과 EOI 뒤 SEF/MPF 자료는 JPEG 본문이 아니다.
    픽셀을 해독하지 않으며, SOF/SOS 구조가 없는 위장 파일은 허용하지 않는다.
    """
    position = 2  # SOI
    frame_components: set[int] = set()
    in_scan = saw_scan = saw_data = False
    while position < len(data):
        if in_scan:
            marker = data.find(b"\xff", position)
            if marker < 0:
                return None
            saw_data = saw_data or marker > position
            position = marker
        elif data[position] != 0xFF:
            return None
        position += 1
        while position < len(data) and data[position] == 0xFF:
            position += 1  # marker fill bytes
        if position == len(data):
            return None
        code = data[position]
        position += 1
        if in_scan and code == 0:
            saw_data = True  # FF00 is an escaped entropy byte, not a marker.
            continue
        if in_scan and 0xD0 <= code <= 0xD7:
            continue  # restart marker: scan continues, no segment length.
        if code == 0x01:
            continue  # TEM has no length.
        if code == 0xD9:
            return position if frame_components and saw_scan and saw_data else None
        if code < 0xC0 or code == 0xD8 or 0xD0 <= code <= 0xD7:
            return None
        in_scan = False
        if position + 2 > len(data):
            return None
        length = int.from_bytes(data[position : position + 2], "big")
        end = position + length
        if length < 2 or end > len(data):
            return None
        if 0xC0 <= code <= 0xCF and code not in {0xC4, 0xC8, 0xCC}:
            if frame_components or length < 11:
                return None
            count = data[position + 7]
            height = int.from_bytes(data[position + 3 : position + 5], "big")
            width = int.from_bytes(data[position + 5 : position + 7], "big")
            if not width or not height or not count or length != 8 + 3 * count:
                return None
            frame_components = set(data[position + 8 : end : 3])
            if len(frame_components) != count:
                return None
        elif code == 0xDA:
            if not frame_components or length < 6:
                return None
            count = data[position + 2]
            components = data[position + 3 : end - 3 : 2]
            if (
                not count
                or length != 6 + 2 * count
                or len(set(components)) != count
                or not set(components) <= frame_components
            ):
                return None
            in_scan = saw_scan = True
        position = end
    return None


def normalize_image(
    data: bytes,
    filename: str,
    *,
    max_bytes: int | None = None,
    label: str = "이미지",
) -> tuple[bytes, str]:
    """원본 크기·컨테이너를 검사하고 JPEG 본문 뒤 자료만 제거한다. 재인코딩하지 않는다."""
    if max_bytes is not None and len(data) > max_bytes:
        raise VisionError(f"{label}: 이미지 크기가 허용 한도를 초과했습니다.", code="image_limit")
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
    elif data.startswith(b"\xff\xd8\xff"):
        end = _jpeg_end(data)
        if end is not None:
            actual = "image/jpeg"
            data = data[:end]
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
            f"{label}: 이미지 확장자와 파일 형식이 일치하지 않거나 파일이 손상되었습니다.",
            code="image_format",
        )
    return data, actual


def image_mime(data: bytes, filename: str) -> str:
    return normalize_image(data, filename)[1]
