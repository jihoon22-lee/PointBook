"""핵심 사용자 흐름 E2E — 구버전 Chromium(약 Chrome 109)으로 Blink 엔진 호환성 검증."""

from conftest import BASE_URL, login, monthly_flow


def test_login_page_and_auth(page):
    page.goto(f"{BASE_URL}/login")
    assert "PointBook" in page.title()
    login(page)
    assert "PointBook" in page.text_content("h1")
    page.goto(f"{BASE_URL}/people")
    assert "인원 관리" in page.text_content("h1")


def test_create_person(page):
    login(page)
    page.goto(f"{BASE_URL}/people/new")
    page.fill('input[name="point_no"]', "0000 0777")
    page.fill('input[name="personal_no"]', "777")
    page.fill('input[name="name"]', "E2E인원")
    page.fill('input[name="grade"]', "소방사")
    page.click('.card button[type="submit"]')
    page.click('button[name="intent"][value="apply"]')
    page.wait_for_url(f"{BASE_URL}/people/*")
    page.goto(f"{BASE_URL}/people")
    assert "E2E인원" in page.text_content("body")
    assert "0000 0777" in page.text_content("body")


def test_monthly_flow_paste_review_confirm(page):
    login(page)
    monthly_flow(page, "778", "E2E소방", "50000", "10000")


def test_dashboard_shows_monthly_data(page):
    login(page)
    monthly_flow(page, "779", "E2E대시", "30000", "5000", month="2099-02")
    page.goto(f"{BASE_URL}/dashboard")
    assert "대시보드" in page.text_content("h1")
    body = page.text_content("body")
    assert "E2E대시" in body
    assert "35,000원" in body
    assert "순사용" in body


def test_person_detail_shows_history(page):
    login(page)
    monthly_flow(page, "780", "E2E이력", "20000", "0", month="2099-03")
    page.goto(f"{BASE_URL}/people")
    page.click('a:has-text("E2E이력")')
    assert "월별 포인트 이력" in page.text_content("body")
    assert "2099-03" in page.text_content("body")
    assert "20,000" in page.text_content("body")


def test_team_views_show_status_counts_active_first_and_total_balance(page):
    login(page)
    monthly_flow(page, "781", "가비재직", "10000", "1000", month="2099-04")
    monthly_flow(page, "782", "하재직", "17000", "3000", month="2099-05")

    page.goto(f"{BASE_URL}/teams")
    team_row = page.locator('tbody tr:has-text("1팀")').first
    team_text = team_row.inner_text()
    assert "전체" in team_text
    assert "재직 1명" in team_text
    assert "비재직" in team_text

    team_row.locator('a:has-text("1팀")').click()
    assert "총잔액" in page.locator("thead").inner_text()
    assert page.locator("tbody tr td:first-child").first.inner_text().strip() == "하재직"
    active_row = page.locator('tbody tr:has-text("하재직")')
    assert active_row.locator("td:nth-child(6)").inner_text().strip() == "20,000원"


def test_review_change_preserves_carry_and_requires_new_analysis(page):
    login(page)
    page.goto(f"{BASE_URL}/monthly")
    page.fill('input[name="month"]', "2099-06")
    page.fill(
        'textarea[name="pasted"]',
        "1팀\t하재직\t소방위\t10000\t782\t00000782\n1팀\t새검수\t소방위\t20000\t783\t00000783",
    )
    page.click('.card button[type="submit"]')
    page.wait_for_selector("#review-form")
    kept_id = page.locator('input[name="row_id_1"]').input_value()
    page.fill('input[name="carry_0"]', "777")
    page.fill('input[name="carry_1"]', "123")
    page.locator("#rows .row-del").first.click()
    assert page.locator(".confirm-monthly").is_disabled()
    assert page.locator('input[name="row_id_0"]').input_value() == kept_id
    assert page.locator('input[name="carry_0"]').input_value() == "123"
    page.click('button:has-text("수정 내용 재검수")')
    page.wait_for_selector('input[name="deactivated_carry_00000782"]')
    assert page.locator('input[name="deactivated_carry_00000782"]').input_value() == ""
    page.fill('input[name="deactivated_carry_00000782"]', "777")
    page.fill('input[name="amount_0"]', "5O000")
    page.click('button:has-text("수정 내용 재검수")')
    assert page.locator('input[name="amount_0"]').input_value() == "5O000"
    assert page.locator('input[name="carry_0"]').input_value() == "123"
    page.fill('input[name="amount_0"]', "20000")
    page.click('button:has-text("수정 내용 재검수")')
    assert page.locator('input[name="deactivated_carry_00000782"]').input_value() == "777"
    page.check('input[name="ack_warnings"]')
    page.click(".confirm-monthly")
    page.wait_for_selector("text=처리가 완료되었습니다", timeout=15000)
