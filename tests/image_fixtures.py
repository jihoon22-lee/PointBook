"""개인 자료 없는 1×1 회색 JPEG. DC 0과 EOB의 1-bit Huffman 부호로 직접 구성한다."""


def jpeg_segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


_TABLES = jpeg_segment(0xDB, b"\x00" + bytes([1]) * 64) + jpeg_segment(
    0xC4,
    # DC table 0: one length-1 code for category 0; AC table 0: one for EOB.
    b"\x00\x01" + bytes(15) + b"\x00\x10\x01" + bytes(15) + b"\x00",
)
_FRAME = b"\x08\x00\x01\x00\x01\x01\x01\x11\x00"
_SCAN = b"\x01\x01\x00"
JPEG = (
    b"\xff\xd8"
    + _TABLES
    + jpeg_segment(0xC0, _FRAME)
    + jpeg_segment(0xDA, _SCAN + b"\x00\x3f\x00")
    + b"\x3f\xff\xd9"
)
PROGRESSIVE_JPEG = (
    b"\xff\xd8"
    + _TABLES
    + jpeg_segment(0xC2, _FRAME)
    + jpeg_segment(0xDA, _SCAN + b"\x00\x00\x00")
    + b"\x7f"
    + jpeg_segment(0xDA, _SCAN + b"\x01\x3f\x00")
    + b"\x7f\xff\xd9"
)
# Trailer contains a second JPEG and ends in an EOI: rfind/end checking is insufficient.
JPEG_TRAILER = b"SEFT synthetic extra data\x00" + JPEG + b"tail\xff\xd9"
