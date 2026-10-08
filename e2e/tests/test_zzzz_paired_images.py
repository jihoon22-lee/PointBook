"""개발 Mock 두 장 입력과 문서 단위 대조 안내의 브라우저 보존."""

import base64

from conftest import BASE_URL, login


def test_paired_images_keep_document_notice_after_first_row_deleted(page):
    login(page)
    page.goto(f"{BASE_URL}/monthly")
    page.fill('input[name="month"]', "2100-10")
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
    )
    for field in ("file", "roster_file"):
        page.set_input_files(
            f'input[name="{field}"]',
            {"name": "synthetic.png", "mimeType": "image/png", "buffer": image},
        )
    page.click('button:has-text("인식 · 검수하기")')
    page.wait_for_selector("#review-form")
    assert page.locator("#rows tbody tr").count() == 3
    assert page.locator("#rows .name-review-warning").count() == 3
    assert page.locator("#rows .name-review-warning").first.is_visible()
    # 기본 Mock의 세 자리 번호는 네 자리 명단에서 자동 보완하지 않는다.
    notice = page.locator('input[name="import_notice"]').input_value()
    assert "자동 연결에 사용하지 않은 3명" in notice
    page.locator("#rows .row-del").first.click()
    with page.expect_navigation():
        page.click('button:has-text("수정 내용 재검수")')
    assert page.locator("#rows tbody tr").count() == 2
    assert page.locator('input[name="import_notice"]').input_value() == notice
    draft_id = page.locator('input[name="draft_id"]').input_value()
    page.goto(f"{BASE_URL}/drafts/{draft_id}")
    assert page.locator("#rows tbody tr").count() == 2
    assert page.locator('input[name="import_notice"]').input_value() == notice
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator("#rows .name-review-warning").count() == 2
    assert page.locator("#rows .name-review-warning").first.is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
