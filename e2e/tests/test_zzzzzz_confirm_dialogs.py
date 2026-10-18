"""확인 대화상자가 문구를 그대로 보여주고 팀 이름이 스크립트로 실행되지 않는지 확인한다."""

from conftest import BASE_URL, login


def test_team_delete_confirmation_shows_name_without_running_it(page):
    login(page)
    messages: list[str] = []
    page.on("dialog", lambda dialog: messages.append(dialog.message))
    name = "합성x'+(localStorage.pbx=1)+'"
    page.goto(f"{BASE_URL}/teams")
    page.fill('[name="name"]', name)
    page.click('button:has-text("추가")')
    row = page.locator("tbody tr", has=page.locator("a", has_text=name))
    row.locator('button:has-text("삭제")').click()
    page.wait_for_url(f"{BASE_URL}/teams")
    assert messages == [
        f"팀 '{name}' 을(를) 삭제할까요? (소속 인원·공용계정은 팀 없음으로 변경됩니다)"
    ]
    assert page.evaluate("localStorage.getItem('pbx')") is None
    assert page.locator("tbody a", has_text=name).count() == 0


def test_ledger_apply_asks_once_and_preview_never(page):
    login(page)
    page.goto(f"{BASE_URL}/people/new")
    page.fill('input[name="point_no"]', "00000883")
    page.fill('input[name="personal_no"]', "883")
    page.fill('input[name="name"]', "E2E확인대화")
    page.click('button[name="intent"][value="preview"]')
    page.click('button[name="intent"][value="apply"]')
    page.wait_for_url("**/people/*")
    page.click('a[href^="/ledger/adjust/"]')
    messages: list[str] = []
    page.on("dialog", lambda dialog: messages.append(dialog.message))
    page.fill('input[name="total"]', "70")
    page.fill('textarea[name="reason"]', "합성 확인 대화")
    page.click('button:has-text("전후 차이 검토")')
    page.wait_for_selector("text=반영 전후")
    assert messages == []
    page.click('button:has-text("검토한 내용 반영")')
    page.wait_for_url("**/ledger/operations/*")
    assert messages == ["표시된 전후 차이와 사유로 반영할까요?"]
