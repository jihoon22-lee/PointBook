"""디자인 적용 검증 스크립트 — 폰트/색상/레이아웃을 계산된 스타일로 확인한다.

실행: docker compose run --rm e2e python3 tests/verify_design.py
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from playwright.sync_api import Page, sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://app:8000")
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}")
        FAILURES.append(label)


def login(page: Page) -> None:
    page.goto(f"{BASE_URL}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "e2e-password")
    page.click('button[type="submit"]')
    page.wait_for_selector("text=PointBook")


def seed_month(page: Page, month: str) -> None:
    """김소방(101)의 해당 월 스냅샷을 생성한다."""
    page.goto(f"{BASE_URL}/monthly")
    page.fill('input[name="month"]', month)
    page.fill('textarea[name="pasted"]', "1팀\t김소방\t소방경\t50000\t101\t")
    page.click('.card button[type="submit"]')
    page.wait_for_selector("text=요청서 검수")
    page.fill('input[name="carry_101|김소방"]', "10000")
    page.locator('button:has-text("확정 · 동기화")').last.click()
    page.wait_for_selector("text=처리가 완료되었습니다")


def verify(label: str, checks: list[tuple[str, Callable[[], bool]]]) -> None:
    print(f"[{label}]")
    for name, fn in checks:
        check(fn(), name)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.on("dialog", lambda d: d.accept())

        login(page)
        page.goto(f"{BASE_URL}/people")
        page.wait_for_selector("text=인원 관리")

        font_ok = page.evaluate(
            "document.fonts.check('16px Pretendard') || document.fonts.check('16px \"Pretendard\"')"
        )
        body_font = page.evaluate("getComputedStyle(document.body).fontFamily")
        verify(
            "폰트",
            [
                ("Pretendard 로드됨", lambda: bool(font_ok)),
                ("body 폰트에 Pretendard 포함", lambda: "Pretendard" in body_font),
            ],
        )

        topbar_bg = page.evaluate(
            "getComputedStyle(document.querySelector('.topbar')).backgroundColor"
        )
        verify(
            "탑바",
            [
                ("네이비 배경 #16273f", lambda: topbar_bg == "rgb(22, 39, 63)"),
                (
                    "인원 페이지 active 강조",
                    lambda: page.evaluate(
                        "document.querySelector('.nav a.active').getAttribute('href') == '/people'"
                    ),
                ),
            ],
        )

        page.goto(f"{BASE_URL}/")
        page.wait_for_selector(".card")
        card_radius = page.evaluate(
            "getComputedStyle(document.querySelector('.card')).borderRadius"
        )
        page.goto(f"{BASE_URL}/people")
        page.wait_for_selector(".btn-primary")
        btn_height = page.evaluate(
            "getComputedStyle(document.querySelector('.btn-primary')).height"
        )
        verify(
            "컴포넌트",
            [
                ("카드 라운드 14px", lambda: card_radius == "14px"),
                ("버튼 터치 타겟 >= 44px", lambda: float(btn_height.replace("px", "")) >= 44),
            ],
        )

        page.goto(f"{BASE_URL}/monthly")
        seed_month(page, "2099-07")
        page.goto(f"{BASE_URL}/monthly")
        page.fill('input[name="month"]', "2099-08")
        page.fill('textarea[name="pasted"]', "1팀\t김소방\t소방경\t50000\t101\t")
        page.click('.card button[type="submit"]')
        page.wait_for_selector("text=요청서 검수")
        placeholder = page.get_attribute('input[name="carry_101|김소방"]', "placeholder")
        verify(
            "검수 화면",
            [("직전 잔액 안내 표시", lambda: "직전 잔액" in (placeholder or ""))],
        )

        overflow = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        verify("데스크톱 레이아웃", [("가로 오버플로 없음", lambda: not overflow)])

        first_row_hit = page.evaluate(
            "(() => { const row = document.querySelector('table tbody tr');"
            " if (!row) return 'no-row';"
            " const r = row.getBoundingClientRect();"
            " const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);"
            " return el ? el.tagName : 'none'; })()"
        )
        verify(
            "테이블 헤더 오버랩 방지",
            [
                (
                    "첫 번째 데이터 행이 헤더에 가려지지 않음 (TD 반환)",
                    lambda: first_row_hit == "TD",
                )
            ],
        )

        mobile = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        mobile.on("dialog", lambda d: d.accept())
        mobile.goto(f"{BASE_URL}/login")
        login_bg = mobile.evaluate("getComputedStyle(document.body).backgroundImage")
        m_overflow_login = mobile.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        login(mobile)
        mobile.goto(f"{BASE_URL}/people")
        m_overflow_people = mobile.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        verify(
            "모바일(390px)",
            [
                ("로그인 배경 그라디언트", lambda: "gradient" in login_bg),
                ("로그인 오버플로 없음", lambda: not m_overflow_login),
                ("인원 목록 오버플로 없음", lambda: not m_overflow_people),
            ],
        )

        browser.close()

    if FAILURES:
        print(f"\n실패 {len(FAILURES)}건: {FAILURES}")
        sys.exit(1)
    print("\n모든 디자인 검증 통과")


if __name__ == "__main__":
    main()
