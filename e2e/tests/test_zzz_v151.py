"""v1.5.1의 실제 오류 칸 이동·초안 연결 검증·저장 화면 전환 회귀."""

from conftest import BASE_URL, login
from test_z_review_choices import upload


def wait_saved(page, response):
    version = str(response.json()["draft_version"])
    page.wait_for_function(
        '(v) => document.querySelector("[name=draft_version]").value === v', arg=version
    )


def test_autosave_does_not_navigate_and_errors_focus_actual_fields(page):
    login(page)
    upload(page, "2110-01", "검토합성팀\t오류이동합성\t소방사\t100\t9801\t00009801")
    page.wait_for_function("typeof window.pointbookDraft !== 'undefined'")
    events = []
    page.on(
        "framenavigated",
        lambda frame: events.append(frame.url) if frame == page.main_frame else None,
    )
    original_url = page.url
    with page.expect_response(lambda r: r.url.endswith("/drafts/save")) as saved:
        page.fill("[name=carry_0]", "50")
    wait_saved(page, saved.value)
    assert page.url == original_url
    assert events == []
    for carry in page.locator('input[name^="deactivated_carry_"]').all():
        carry.fill("0")
    page.fill("[name=carry_0]", "12.5")
    if page.locator("[name=ack_warnings]").count():
        page.check("[name=ack_warnings]")
    with page.expect_response(lambda r: r.url.endswith("/monthly/confirm")) as failed:
        page.click(".confirm-monthly")
    assert failed.value.status == 400
    page.locator('#confirm-errors [data-error-field="carry_0"]').click()
    assert page.evaluate("document.activeElement.name") == "carry_0"
    assert page.input_value("[name=carry_0]") == "12.5"
    page.fill("[name=carry_0]", "0")
    page.fill("[name=amount_0]", "5O000")
    with page.expect_response(lambda r: r.url.endswith("/monthly/review")):
        page.click("#review-again")
    page.locator('#confirm-errors [data-error-field="amount_0"]').click()
    assert page.evaluate("document.activeElement.name") == "amount_0"
    assert page.input_value("[name=amount_0]") == "5O000"
    page.fill("[name=amount_0]", "100")
    page.fill("[name=personal_no_0]", "")
    with page.expect_response(lambda r: r.url.endswith("/monthly/review")):
        page.click("#review-again")
    page.check("#focus-input")
    page.locator('#confirm-errors [data-error-field="personal_no_0"]').click()
    assert page.evaluate("document.activeElement.name") == "personal_no_0"
    assert not page.locator("#focus-input").is_checked()


def test_unbound_absent_carry_is_preserved_separately_and_requires_fresh_input(page):
    login(page)
    upload(page, "2110-02", "검토합성팀\t보존확인합성\t소방사\t100\t9802\t00009802")
    target = page.locator('#balances-inactive_kept input[name^="deactivated_carry_"]').first
    assert target.count() == 1
    name = target.get_attribute("name")
    for carry in page.locator('input[name^="deactivated_carry_"]').all():
        carry.fill("0")
    # 이전 버전 탭에는 인원 확인 서명이 없다. 예전 입력을 새 인원에게 묵시적으로 붙이면 안 된다.
    target.fill("4567")
    binding_name = name.replace("deactivated_carry_", "absent_binding_")
    page.locator('[name="' + binding_name + '"]').evaluate("input => input.remove()")
    with page.expect_response(lambda r: r.url.endswith("/drafts/save")) as saved:
        page.click("#draft-save")
    wait_saved(page, saved.value)
    page.reload()
    assert page.input_value('[name="' + name + '"]') == ""
    preserved = page.locator("#preserved-absent-carries")
    assert "4,567" in preserved.inner_text() or "4567" in preserved.inner_text()
    assert "자동 적용되지 않음" in preserved.inner_text()
    page.fill('[name="' + name + '"]', "7654")
    with page.expect_response(lambda r: r.url.endswith("/drafts/save")) as saved:
        page.click("#draft-save")
    wait_saved(page, saved.value)
    page.reload()
    assert page.input_value('[name="' + name + '"]') == "7,654"
    assert page.locator("#preserved-absent-carries input[name=preserved_absent_carry]").count() == 1


def test_ledger_list_and_detail_use_same_korean_time(page):
    login(page)
    page.goto(f"{BASE_URL}/ledger")
    assert "기록 시각 (한국 시간)" in page.locator("thead").inner_text()
    first = page.locator("tbody tr").first
    timestamp = first.locator("td").first.inner_text()
    first.locator("a").click()
    assert f"기록 시각: {timestamp} (한국 시간)" in page.inner_text("body")
