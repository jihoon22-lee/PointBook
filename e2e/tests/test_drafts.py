"""구버전 Chromium의 초안 자동 저장·충돌·집중 입력·재로그인 흐름."""

from conftest import BASE_URL, login


def open_draft(page, month="2080-02"):
    page.goto(f"{BASE_URL}/monthly")
    page.fill('input[name="month"]', month)
    page.fill(
        'textarea[name="pasted"]',
        "팀\t나합성초안\t\t100\t901\t00000901\t첫메모\n팀\t가합성초안\t\t200\t902\t00000902\t둘메모",
    )
    page.click('#monthly-upload button[type="submit"]')
    page.wait_for_url("**/drafts/*")
    return page.url


def saved(page):
    page.wait_for_function(
        "document.getElementById('draft-status').textContent.indexOf('저장 완료') >= 0"
    )


def test_autosave_refresh_other_device_sort_filter_and_confirm(page, browser):
    login(page)
    url = open_draft(page, "2080-01")
    row_id = page.input_value('input[name="row_id_0"]')
    page.fill('input[name="carry_0"]', "11")
    page.fill('input[name="carry_1"]', "22")
    page.check("#focus-input")
    page.select_option("#row-sort", "name")
    page.fill(f'tr[data-row-id="{row_id}"] input[data-field="carry"]', "33")
    saved(page)
    page.reload()
    assert page.input_value('input[name="row_id_0"]') == row_id
    assert page.input_value('input[name="carry_0"]') == "33"
    assert page.input_value('input[name="carry_1"]') == "22"
    assert page.evaluate("Object.keys(localStorage).length") == 0
    other = browser.new_context(storage_state=page.context.storage_state())
    try:
        device = other.new_page()
        device.goto(url)
        assert device.input_value('input[name="carry_0"]') == "33"
    finally:
        other.close()
    page.fill('input[name="carry_0"]', "34")
    page.press('input[name="carry_0"]', "Enter")
    assert page.evaluate("document.activeElement.name") == "carry_1"
    page.select_option("#row-filter", "missing")
    assert page.locator("#rows tbody tr:visible").count() == 0
    acknowledgement = page.locator('[name="ack_warnings"]')
    if acknowledgement.count():
        acknowledgement.check()
    page.click("button.confirm-monthly")
    page.wait_for_selector("text=처리가 완료되었습니다")
    page.goto(f"{BASE_URL}/dashboard?month=2080-01&account_type=all")
    assert "나합성초안" in page.text_content("body") and "가합성초안" in page.text_content("body")
    assert "356원" in page.text_content("body")


def test_two_tabs_conflict_pauses_overwrite_and_fork_preserves_input(page):
    login(page)
    url = open_draft(page)
    other = page.context.new_page()
    other.on("dialog", lambda dialog: dialog.accept())
    try:
        other.goto(url)
        page.fill('input[name="carry_0"]', "41")
        saved(page)
        other.fill('input[name="carry_0"]', "99")
        other.wait_for_selector('#draft-status:has-text("다른 탭")')
        page.reload()
        assert page.input_value('input[name="carry_0"]') == "41"
        other.click("#draft-fork")
        saved(other)
        assert other.url != url
        assert other.input_value('input[name="carry_0"]') == "99"
    finally:
        other.close()


def test_failed_autosave_and_expired_session_can_resume_without_losing_dom(page):
    login(page)
    open_draft(page, "2080-03")
    page.route(
        "**/drafts/save",
        lambda route: route.fulfill(
            status=500, content_type="application/json", body='{"error":"합성 저장 실패"}'
        ),
    )
    page.fill('input[name="carry_0"]', "55")
    page.wait_for_selector('#draft-status:has-text("합성 저장 실패")')
    assert page.input_value('input[name="carry_0"]') == "55"
    page.unroute("**/drafts/save")
    page.click("#draft-save")
    saved(page)
    page.context.clear_cookies()
    page.fill('input[name="carry_0"]', "66")
    page.wait_for_selector("#draft-login:visible")
    relogin = page.context.new_page()
    login(relogin)
    relogin.close()
    page.click("#draft-save")
    saved(page)
    page.reload()
    assert page.input_value('input[name="carry_0"]') == "66"


def test_korean_composition_defers_autosave_and_mobile_focus(page):
    page.set_viewport_size({"width": 390, "height": 844})
    login(page)
    open_draft(page, "2080-04")
    field = page.locator('input[name="name_0"]')
    before = page.input_value('[name="draft_version"]')
    field.dispatch_event("compositionstart")
    field.fill("한글합성")
    page.evaluate("() => new Promise(resolve => setTimeout(resolve, 1000))")
    assert page.input_value('[name="draft_version"]') == before
    field.dispatch_event("compositionend")
    saved(page)
    page.check("#focus-input")
    page.click("#next-missing")
    assert page.evaluate("document.activeElement.name") == "carry_0"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.uncheck("#focus-input")
    page.click("#add-row")
    added_note = page.locator("#rows tbody tr").last.locator('[data-field="note"]')
    added_note.fill("첫째 줄")
    added_note.press("End")
    added_note.press("Enter")
    added_note.type("둘째 줄")
    assert added_note.input_value() == "첫째 줄\n둘째 줄"
    saved(page)
    page.reload()
    assert (
        page.locator("#rows tbody tr").last.locator('[data-field="note"]').input_value()
        == "첫째 줄\n둘째 줄"
    )
