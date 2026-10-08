"""JPEG 본문 경계·크기·AI 전송 검증. 원본 사진이나 외부 API를 사용하지 않는다."""

import asyncio
import base64
import json

import httpx
import pytest

from app.ai.base import VisionError, VisionProvider
from app.ai.gemini import GeminiProvider
from app.ai.images import image_mime, normalize_image
from app.config import Settings
from app.services import vision
from app.services.parsing import RawRequestRow
from tests.image_fixtures import JPEG, JPEG_TRAILER, PROGRESSIVE_JPEG, jpeg_segment
from tests.test_gemini import ROW, envelope
from tests.test_vision_boundary import HEIC, PNG, WEBP


@pytest.mark.parametrize("jpeg", [JPEG, PROGRESSIVE_JPEG])
@pytest.mark.parametrize("trailer", [b"", b"SEFT", JPEG_TRAILER])
def test_jpeg_primary_boundary_preserves_original_bytes(jpeg, trailer):
    normalized, mime = normalize_image(jpeg + trailer, "image.JPEG")
    assert normalized == jpeg and mime == "image/jpeg"
    assert image_mime(jpeg + trailer, "image.jpg") == mime


def test_app_thumbnail_comment_and_scan_escapes_are_not_main_eoi():
    # APP/COM contents are opaque length-prefixed data, even with marker-looking bytes.
    prefix = jpeg_segment(0xE1, b"Exif\x00\x00" + JPEG) + jpeg_segment(0xFE, b"\xff\xd9")
    body = JPEG[:2] + prefix + JPEG[2:-2] + b"\xff\x00\xd9\xff\xd0\x12\xff\xff\xd9"
    assert normalize_image(body + JPEG_TRAILER, "image.jpg")[0] == body


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xd8\xff\xe0synthetic-jpeg\xff\xd9",
        b"\xff\xd8\xff\xd9",
        b"\xff\xd8" + jpeg_segment(0xE1, JPEG) + b"\xff\xd9",
        b"\xff\xd8\xff\xe1\x00\x01\xff\xd9",
        b"\xff\xd8\xff\xe1\xff\xff\xff\xd9",
        JPEG[:-2],
        JPEG[:-1],
        JPEG[:-3] + b"\xff\xd9",  # no entropy data
        JPEG[:-2] + b"\xff\x00\xd9",
        JPEG[:-2] + b"\xff\xd8" + JPEG_TRAILER,
        JPEG.replace(b"\xff\xda\x00\x08", b"\xff\xda\x00\x07"),
        JPEG.replace(b"\xff\xc0\x00\x0b", b"\xff\xc0\x00\x0a"),
        JPEG.replace(b"\x08\x00\x01\x00\x01", b"\x08\x00\x00\x00\x01"),
    ],
)
def test_truncated_or_forged_jpeg_is_rejected(data):
    with pytest.raises(VisionError, match="파일 형식"):
        normalize_image(data, "image.jpg")


@pytest.mark.parametrize("filename", ["image.png", "image.webp", "image.heic", "image.svg"])
def test_trailer_cannot_bypass_extension_check(filename):
    with pytest.raises(VisionError, match="파일 형식"):
        normalize_image(JPEG + JPEG_TRAILER, filename)


@pytest.mark.parametrize("data,filename", [(PNG, "a.png"), (WEBP, "a.webp"), (HEIC, "a.heic")])
def test_non_jpeg_formats_remain_byte_identical(data, filename):
    assert normalize_image(data, filename)[0] is data


class CapturingProvider(VisionProvider):
    supports_name_recheck = True

    def __init__(self):
        self.calls = []

    def extract_table(self, data, filename):
        self.calls.append(("payment", data))
        return [RawRequestRow(name="합성", team="1팀", grade="소방교", amount="100")]

    def extract_roster(self, data, filename):
        self.calls.append(("roster", data))
        return [RawRequestRow(name="합성", team="1팀", grade="소방교", personal_no="0012")]

    def extract_names(self, data, filename, *, roster=False):
        self.calls.append(("name-roster" if roster else "name-payment", data))
        return [
            RawRequestRow(
                name="합성",
                team="1팀",
                grade="소방교",
                amount="" if roster else "100",
                personal_no="0012" if roster else "",
            )
        ]


@pytest.mark.parametrize("paired", [False, True])
def test_single_pair_and_name_rechecks_receive_only_normalized_main_image(monkeypatch, paired):
    provider = CapturingProvider()
    monkeypatch.setattr(vision, "get_provider", lambda: provider)
    if paired:
        asyncio.run(
            vision.extract_image_pair(JPEG + JPEG_TRAILER, "a.jpg", JPEG + b"SEFT", "b.jpg")
        )
        assert [call[0] for call in provider.calls] == [
            "payment",
            "roster",
            "name-payment",
            "name-roster",
        ]
    else:
        asyncio.run(vision.extract_image(JPEG + JPEG_TRAILER, "a.jpg"))
        assert [call[0] for call in provider.calls] == ["payment", "name-payment"]
    assert all(data == JPEG for _, data in provider.calls)


@pytest.mark.parametrize(
    "method,options",
    [
        ("extract_table", {}),
        ("extract_roster", {}),
        ("extract_names", {}),
        ("extract_names", {"roster": True}),
    ],
)
def test_direct_gemini_payload_is_normalized_for_every_pass(method, options):
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json=envelope(json.dumps([ROW])))

    provider = GeminiProvider("synthetic", transport=httpx.MockTransport(respond))
    getattr(provider, method)(JPEG + JPEG_TRAILER, "source.jpg", **options)
    inline = payloads[0]["contents"][0]["parts"][1]["inline_data"]
    assert inline["mime_type"] == "image/jpeg"
    assert base64.b64decode(inline["data"]) == JPEG


def test_original_size_is_checked_before_stripping_trailer(monkeypatch):
    oversized = JPEG + bytes(1024 * 1024)
    monkeypatch.setattr(vision, "get_settings", lambda: Settings(max_upload_mb=1))
    monkeypatch.setattr(vision, "get_provider", lambda: pytest.fail("AI must not run"))
    with pytest.raises(VisionError, match="이미지 크기"):
        asyncio.run(vision.extract_image(oversized, "source.jpg"))
    monkeypatch.setattr("app.ai.gemini.get_settings", lambda: Settings(max_upload_mb=1))
    provider = GeminiProvider(
        "synthetic", transport=httpx.MockTransport(lambda request: pytest.fail("AI must not run"))
    )
    with pytest.raises(VisionError, match="이미지 크기"):
        provider.extract_names(oversized, "source.jpg")


@pytest.mark.parametrize("invalid_role", ["file", "roster_file"])
def test_http_invalid_image_identifies_role_before_any_external_call(
    auth_client, monkeypatch, invalid_role
):
    monkeypatch.setattr(vision, "get_provider", lambda: pytest.fail("AI must not run"))
    files = {"file": ("a.jpg", JPEG + JPEG_TRAILER), "roster_file": ("b.jpg", JPEG + JPEG_TRAILER)}
    files[invalid_role] = ("bad.jpg", JPEG[:-2])
    response = auth_client.post("/monthly/upload", data={"month": "2026-10"}, files=files)
    assert response.status_code == 400
    assert ("요청서 사진:" if invalid_role == "file" else "개인번호 명단 사진:") in response.text


def test_http_paired_jpeg_metadata_reaches_review_and_keeps_upload_values(auth_client, monkeypatch):
    provider = CapturingProvider()
    monkeypatch.setattr(vision, "get_provider", lambda: provider)
    response = auth_client.post(
        "/monthly/upload",
        data={"month": "2026-10"},
        files={
            "file": ("a.jpg", JPEG + JPEG_TRAILER),
            "roster_file": ("b.jpg", JPEG + JPEG_TRAILER),
        },
    )
    assert 'name="name_0" value="합성"' in response.text
    assert 'name="personal_no_0" value="0012"' in response.text
    assert len(provider.calls) == 4 and all(data == JPEG for _, data in provider.calls)
