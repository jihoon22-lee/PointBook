"""합성 장부로 고정 제목·최대 placeholder·입력 편의와 확정 안내를 검증한다."""

from conftest import login
from test_z_review_choices import confirm_seed, upload


def test_money_headers_placeholders_and_confirmation_feedback(page):
    login(page)
    lines = [
        f"표시합성팀\t표시합성{i:02d}\t소방사\t{[0, 1000000, 10000000][i] if i < 3 else 100}\t{9100 + i}\t{9100 + i:08d}"
        for i in range(30)
    ]
    upload(page, "2102-01", "\n".join(lines))
    confirm_seed(page)
    upload(page, "2102-02", "\n".join(lines))
    assert page.locator("#rows thead th").all_text_contents()[5:7] == ["이월 잔액", "충전액"]
    for i, value in enumerate(("0", "1,000,000", "10,000,000")):
        assert page.locator(f'[name="carry_{i}"]').get_attribute("placeholder") == value
    for width in (390, 800, 1280, 1920):
        page.set_viewport_size({"width": width, "height": 844})
        for focus in (False, True):
            page.locator("#focus-input").set_checked(focus)
            metrics = page.locator('[name="carry_2"]').evaluate("""input => {
                const style = getComputedStyle(input), canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d'); ctx.font = style.font;
                return {text: ctx.measureText(input.placeholder).width,
                  available: input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)};
            }""")
            assert metrics["text"] <= metrics["available"], metrics
            page.locator('[name="carry_2"]').focus()
            page.wait_for_timeout(50)
            visible = page.locator('[name="carry_2"]').evaluate("""input => {
                const box = input.getBoundingClientRect();
                const first = input.closest('tr').cells[0].getBoundingClientRect();
                const wrap = input.closest('.table-wrap').getBoundingClientRect();
                return {left: box.left, right: box.right, fixed: first.right, edge: wrap.right};
            }""")
            assert visible["left"] >= visible["fixed"] and visible["right"] <= visible["edge"], (
                visible
            )
            page.locator('[name="carry_15"]').evaluate(
                "input => input.scrollIntoView({block: 'center'})"
            )
            page.locator("#rows").evaluate("table => table.closest('.table-wrap').scrollLeft = 190")
            page.wait_for_function("!document.getElementById('review-floating-header').hidden")
            page.wait_for_timeout(50)
            positions = page.evaluate("""() => {
              const live = document.querySelector('#rows th.money-carry').getBoundingClientRect();
              const fixed = document.querySelector('#review-floating-header th.money-carry').getBoundingClientRect();
              return {live: live.x, fixed: fixed.x, top: fixed.top, nav: document.querySelector('.topbar').getBoundingClientRect().bottom};
            }""")
            assert abs(positions["live"] - positions["fixed"]) < 2, positions
            assert abs(positions["top"] - positions["nav"]) < 2, positions
    page.set_viewport_size({"width": 1280, "height": 844})
    page.uncheck("#focus-input")
    carry = page.locator('[name="carry_0"]')
    carry.fill("1000000")
    assert carry.input_value() == "1,000,000"
    carry.evaluate("input => input.setSelectionRange(2, 2)")
    carry.press("Backspace")
    assert carry.input_value() == "000,000"
    carry.fill("")
    carry.type("10000000")
    assert carry.input_value() == "10,000,000"
    carry.evaluate("""input => {
      const data = new DataTransfer(); data.setData('text', '12,34');
      input.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, clipboardData: data}));
      input.value = '12,34'; input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertFromPaste'}));
    }""")
    assert carry.input_value() == "12,34"
    carry.fill("1000")
    page.click(".confirm-monthly")
    assert "이월 잔액" in page.locator("#confirm-message").inner_text()
    assert page.evaluate("document.activeElement.name") == "carry_1"
    for field in page.locator('input[name^="carry_"],input[name^="deactivated_carry_"]').all():
        field.fill("0")
    acknowledgement = page.locator('[name="ack_warnings"]')
    if acknowledgement.count():
        page.click(".confirm-monthly")
        assert "확인란" in page.locator("#confirm-message").inner_text()
    page.fill('[name="amount_0"]', "1234567")
    assert page.locator('[name="amount_0"]').input_value() == "1,234,567"
    assert page.locator(".confirm-monthly").is_disabled()
    assert "재검수" in page.locator("#confirm-reason").inner_text()
    with page.expect_response(lambda response: response.url.endswith("/monthly/review")):
        page.click("#review-again")
    assert page.locator('[name="amount_0"]').input_value() == "1,234,567"
    page.route(
        "**/drafts/save",
        lambda route: route.fulfill(
            status=409,
            content_type="application/json",
            body='{"error":"다른 기기에서 초안이 변경되었습니다. 다시 열어 확인하세요."}',
        ),
    )
    page.fill('[name="carry_0"]', "999")
    page.wait_for_function(
        "document.getElementById('confirm-message').textContent.includes('다른 기기')"
    )
    assert page.locator("#confirm-feedback").is_visible()
    assert page.locator('[name="carry_0"]').input_value() == "999"
    page.unroute("**/drafts/save")
    page.click("#draft-save")
    page.wait_for_function(
        "document.getElementById('draft-status').textContent.includes('저장 완료')"
    )
    assert page.locator("#confirm-feedback").is_hidden()


def test_team_color_autosave_and_mobile_totals(page):
    from conftest import BASE_URL

    login(page)
    page.goto(f"{BASE_URL}/teams")
    page.fill('[name="name"]', "색상합성팀")
    page.click('button:has-text("추가")')
    row = page.locator("tr", has=page.locator("a", has_text="색상합성팀"))
    color = row.locator('[name="color"]')
    assert row.locator('button:has-text("색상 저장")').count() == 0
    color.evaluate(
        "input => { input.value = '#123456'; input.dispatchEvent(new Event('change', {bubbles:true})); }"
    )
    page.wait_for_function(
        "Array.from(document.querySelectorAll('.team-color-status')).some(node => node.textContent === '저장됨')"
    )
    assert color.input_value() == "#123456"
    page.reload()
    assert color.input_value() == "#123456"
    page.route("**/teams/*/color", lambda route: route.fulfill(status=500, body="failure"))
    color.evaluate(
        "input => { input.value = '#abcdef'; input.dispatchEvent(new Event('change', {bubbles:true})); }"
    )
    row.locator(".team-color-status", has_text="저장하지 못했습니다").wait_for()
    assert color.input_value() == "#123456"
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert "재직 총 잔액" in page.locator("thead").inner_text()
    assert "전체 총 잔액" in page.locator("thead").inner_text()
