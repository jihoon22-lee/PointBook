"""디자인 검증용 스크린샷 캡처 스크립트 (구버전 Chromium).

실행: docker compose run --rm e2e python tests/screenshots.py
데이터 시드(로그인 → 월간 처리 확정) 후 각 페이지를 캡처한다.
"""

import os

from playwright.sync_api import Browser, Page, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://app:8000")
OUT = "/app/e2e/screenshots"


def _login(page: Page) -> None:
    page.goto(f"{BASE_URL}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "e2e-password")
    page.click('button[type="submit"]')
    page.wait_for_selector("text=PointBook")


def _seed(page: Page) -> None:
    """인원 3명 + 2099-07 월간 처리 확정."""
    page.goto(f"{BASE_URL}/monthly")
    page.fill('input[name="month"]', "2099-07")
    pasted = "1팀\t김소방\t소방경\t50000\t101\t\n2팀\t이소방\t소방위\t40000\t102\t비고\n1팀\t박소방\t소방사\t30000\t103\t"
    page.fill('textarea[name="pasted"]', pasted)
    page.click('.card button[type="submit"]')
    page.wait_for_selector("text=요청서 검수")
    page.fill('input[name="carry_101|김소방"]', "10000")
    page.fill('input[name="carry_102|이소방"]', "5000")
    page.fill('input[name="carry_103|박소방"]', "2000")
    page.locator('button:has-text("확정 · 동기화")').last.click()
    page.wait_for_selector("text=처리가 완료되었습니다")


def _shot(page: Page, name: str) -> None:
    page.screenshot(path=f"{OUT}/{name}.png", full_page=True)
    print(f"saved {name}.png")


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        browser: Browser = p.chromium.launch()
        desktop = browser.new_context(viewport={"width": 1280, "height": 900})
        page = desktop.new_page()
        page.on("dialog", lambda d: d.accept())

        page.goto(f"{BASE_URL}/login")
        _shot(page, "01-login")
        _login(page)
        _seed(page)

        page.goto(f"{BASE_URL}/")
        _shot(page, "02-home")
        page.goto(f"{BASE_URL}/people")
        _shot(page, "03-people")
        page.goto(f"{BASE_URL}/teams")
        _shot(page, "04-teams")
        page.goto(f"{BASE_URL}/monthly")
        _shot(page, "05-monthly")
        page.goto(f"{BASE_URL}/monthly")
        page.fill('textarea[name="pasted"]', "1팀\t김소방\t소방경\t50000\t101\t")
        page.click('.card button[type="submit"]')
        page.wait_for_selector("text=요청서 검수")
        _shot(page, "06-review")
        page.goto(f"{BASE_URL}/dashboard")
        _shot(page, "07-dashboard")
        page.goto(f"{BASE_URL}/people/1")
        _shot(page, "08-person-detail")
        page.goto(f"{BASE_URL}/people/new")
        _shot(page, "09-person-form")

        mobile = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        page = mobile.new_page()
        page.on("dialog", lambda d: d.accept())
        _login(page)
        page.goto(f"{BASE_URL}/people")
        _shot(page, "10-mobile-people")
        page.goto(f"{BASE_URL}/monthly")
        page.fill('textarea[name="pasted"]', "1팀\t김소방\t소방경\t50000\t101\t")
        page.click('.card button[type="submit"]')
        page.wait_for_selector("text=요청서 검수")
        _shot(page, "11-mobile-review")
        page.goto(f"{BASE_URL}/dashboard")
        _shot(page, "12-mobile-dashboard")

        browser.close()
        print("done")


if __name__ == "__main__":
    main()
