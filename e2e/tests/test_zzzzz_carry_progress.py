"""직전 잔액 복사·초안 재개·두 비재직 구역 상한의 실제 브라우저 흐름."""

from conftest import login
from test_z_review_choices import confirm_seed, upload
from test_zzz_v151 import wait_saved


def test_copy_previous_and_progress_restore_with_absent_limits(page):
    login(page)
    lines = [
        f"잔액합성팀\t잔액합성{i}\t소방위\t{100 * i if i < 4 else 0}\t{9900 + i}\t{9900 + i:08d}"
        for i in (1, 2, 3, 4)
    ]
    upload(page, "2120-01", "\n".join(lines))
    assert page.locator("#rows .use-previous-carry:enabled").count() == 0
    confirm_seed(page)
    upload(page, "2120-02", "\n".join(lines[:2]))
    for field in page.locator('input[name^="carry_"],input[name^="deactivated_carry_"]').all():
        field.fill("0")
    page.fill('[name="deactivated_carry_00009903"]', "200")
    page.check('[name="ack_warnings"]')
    page.click(".confirm-monthly")
    page.wait_for_selector("text=처리가 완료되었습니다")
    upload(page, "2120-03", lines[0])
    current = page.locator('[name="carry_0"]')
    new_absent = page.locator('[name="deactivated_carry_00009902"]')
    old_absent = page.locator('[name="deactivated_carry_00009903"]')
    assert current.input_value() == new_absent.input_value() == old_absent.input_value() == ""
    assert "직전 총 잔액: 200원" in old_absent.locator("..").inner_text()
    assert "직전 총 잔액: 200원" in new_absent.locator("..").inner_text()
    for field in (current, new_absent, old_absent):
        field.locator("..").locator(".use-previous-carry").click()
        assert "입력 완료" in field.locator("..").inner_text()
    assert current.input_value() == "100"
    assert new_absent.input_value() == old_absent.input_value() == "200"
    with page.expect_response(lambda r: r.url.endswith("/drafts/save")) as saved:
        page.click("#draft-save")
    wait_saved(page, saved.value)
    page.reload()
    assert current.input_value() == "100"
    assert new_absent.input_value() == old_absent.input_value() == "200"
    assert "입력 완료 3명" in page.locator("#input-summary").inner_text()

    new_absent.fill("201")
    old_absent.fill("201")
    page.click("#next-carry-error")
    assert page.evaluate("document.activeElement.name") == "deactivated_carry_00009902"
    page.click("#next-carry-error")
    assert page.evaluate("document.activeElement.name") == "deactivated_carry_00009903"
    for field in (new_absent, old_absent):
        field.locator("..").locator(".use-previous-carry").click()
    for field in (new_absent, old_absent):
        field.fill("201")
        assert field.get_attribute("aria-invalid") == "true"
        assert "직전 총 잔액보다 1원 많습니다" in field.locator("..").inner_text()
        assert page.locator(".confirm-monthly").is_disabled()
        page.click("#next-carry-error")
        assert page.evaluate("document.activeElement.name") == field.get_attribute("name")
        field.locator("..").locator(".use-previous-carry").click()
        assert field.get_attribute("aria-invalid") == "false"

    # 확정 버튼을 우회하는 요청도 서버가 막으며 초안 금액은 보존한다.
    old_absent.fill("201")
    with page.expect_response(lambda r: r.url.endswith("/drafts/save")) as saved:
        page.click("#draft-save")
    wait_saved(page, saved.value)
    page.reload()
    assert old_absent.input_value() == "201"
    assert "금액 확인 필요 1명" in page.locator("#input-summary").inner_text()
    blocked = page.evaluate("""async () => {
      const form = document.getElementById('review-form');
      const data = new FormData(form); data.set('ack_warnings', 'yes');
      const response = await fetch('/monthly/confirm', {method: 'POST', body: data});
      return {status: response.status, text: await response.text()};
    }""")
    assert blocked["status"] == 400 and "직전 총 잔액보다 1원 많습니다" in blocked["text"]
    old_absent.fill("200")
    page.select_option("#row-filter", "missing")
    assert not old_absent.is_visible()
    # 숨긴 완료 입력도 집계하며 미입력 구역으로 이동하면 필터가 풀린다.
    assert "입력 완료 3명" in page.locator("#input-summary").inner_text()
    page.click("#next-missing")
    assert page.locator("#row-filter").input_value() == "all"
    for field in page.locator('input[name^="deactivated_carry_"]:not([type="hidden"])').all():
        field.locator("..").locator(".use-previous-carry").click()
    assert "미입력 0명" in page.locator("#input-summary").inner_text()
    # 실제 0원 참고값도 버튼으로 복사할 수 있고 완료로 센다.
    zero = page.locator('input[data-limit-previous="yes"][data-previous-total="0"]').first
    assert zero.count() == 1 and zero.input_value() == "0"
    current.fill("12.5")
    assert "금액 확인 필요" in current.locator("..").inner_text()
    current.locator("..").locator(".use-previous-carry").click()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.fill('[name="name_0"]', "연결변경합성")
    assert current.locator("..").locator(".use-previous-carry").is_disabled()
    assert "재검수 후 확인" in current.locator("..").inner_text()
