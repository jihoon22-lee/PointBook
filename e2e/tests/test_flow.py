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
    page.fill('input[name="personal_no"]', "777")
    page.fill('input[name="name"]', "E2E인원")
    page.fill('input[name="grade"]', "소방사")
    page.click('.card button[type="submit"]')
    page.wait_for_url(f"{BASE_URL}/people/*")
    page.goto(f"{BASE_URL}/people")
    assert "E2E인원" in page.text_content("body")


def test_monthly_flow_paste_review_confirm(page):
    login(page)
    monthly_flow(page, "778", "E2E소방", "50000", "10000")


def test_dashboard_shows_monthly_data(page):
    login(page)
    monthly_flow(page, "779", "E2E대시", "30000", "5000", month="2026-08")
    page.goto(f"{BASE_URL}/dashboard")
    assert "대시보드" in page.text_content("h1")
    body = page.text_content("body")
    assert "E2E대시" in body
    assert "35,000원" in body


def test_person_detail_shows_history(page):
    login(page)
    monthly_flow(page, "780", "E2E이력", "20000", "0", month="2026-09")
    page.goto(f"{BASE_URL}/people")
    page.click('a:has-text("E2E이력")')
    assert "월별 포인트 이력" in page.text_content("body")
    assert "2026-09" in page.text_content("body")
    assert "20,000" in page.text_content("body")
