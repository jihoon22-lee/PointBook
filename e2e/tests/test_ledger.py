"""정정 승인과 별도 현재 관측의 실제 브라우저 흐름."""

from conftest import BASE_URL, login, monthly_flow


def test_correction_review_and_immutable_result(page):
    login(page)
    monthly_flow(page, "881", "E2E정정", "100", "20", month="2099-12")
    page.goto(f"{BASE_URL}/people")
    page.click('a:has-text("E2E정정")')
    person_url = page.url
    page.click('a[href^="/ledger/correct/"]')
    page.fill('input[name="carry"]', "30")
    page.fill('input[name="amount"]', "150")
    page.fill('input[name="note"]', "E2E 정정 비고")
    page.fill('textarea[name="reason"]', "합성 원본 대조")
    page.click('button:has-text("전후 차이 검토")')
    assert "반영 전후" in page.text_content("body")
    page.click('button:has-text("검토한 내용 반영")')
    page.wait_for_url("**/ledger/operations/*")
    assert "합성 원본 대조" in page.text_content("body")
    assert "E2E 정정 비고" in page.text_content("body")
    page.goto(person_url)
    assert "180원" in page.text_content("body")
    page.goto(f"{BASE_URL}/ledger/integrity")
    assert "계산·연결 정합성 검사 통과" in page.text_content("body")


def test_current_adjustment_on_mobile_is_separate(page):
    page.set_viewport_size({"width": 390, "height": 844})
    login(page)
    page.goto(f"{BASE_URL}/people/new")
    page.fill('input[name="point_no"]', "00000882")
    page.fill('input[name="personal_no"]', "882")
    page.fill('input[name="name"]', "E2E현재보정")
    page.click('button[name="intent"][value="preview"]')
    page.click('button[name="intent"][value="apply"]')
    page.wait_for_url("**/people/*")
    page.click('a[href^="/ledger/adjust/"]')
    page.fill('input[name="total"]', "150")
    page.fill('textarea[name="reason"]', "합성 잔액 관측")
    page.click('button:has-text("전후 차이 검토")')
    page.click('button:has-text("검토한 내용 반영")')
    page.wait_for_url("**/ledger/operations/*")
    assert "150원" in page.text_content("body")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
