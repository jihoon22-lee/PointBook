import os

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "e2e-password")


@pytest.fixture(scope="session")
def browser() -> Browser:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture()
def page(browser: Browser) -> Page:
    context = browser.new_context()
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())
    yield page
    context.close()


def login(page: Page) -> None:
    page.goto(f"{BASE_URL}/login")
    page.fill('input[name="username"]', ADMIN_USERNAME)
    page.fill('input[name="password"]', ADMIN_PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_selector("text=PointBook")


def monthly_flow(
    page: Page, personal_no: str, name: str, amount: str, carry: str, month: str = "2099-01"
) -> None:
    """붙여넣기 → 검수 → 확정까지의 월간 처리 전체 흐름."""
    page.goto(f"{BASE_URL}/monthly")
    page.fill('input[name="month"]', month)
    page.fill('textarea[name="pasted"]', f"1팀\t{name}\t소방위\t{amount}\t{personal_no}\t")
    page.click('.card button[type="submit"]')
    page.wait_for_selector("text=요청서 검수")
    page.fill(f'input[name="carry_{personal_no}|{name}"]', carry)
    page.locator('button:has-text("확정 · 동기화")').last.click()
    page.wait_for_selector("text=처리가 완료되었습니다", timeout=15000)
