"""구버전 Chromium에서 키보드 정렬·추가 행 라벨·빈 요청서 버튼·모바일 메뉴를 확인한다."""

from conftest import BASE_URL, login, monthly_flow


def test_dashboard_sort_works_from_keyboard(page):
    login(page)
    monthly_flow(page, "884", "E2E정렬", "10000", "0", month="2121-01")
    page.goto(f"{BASE_URL}/dashboard?month=2121-01&account_type=all")
    header = page.locator('#person-stats th[data-sort="name"]')
    button = header.locator("button.sort-button")
    assert button.inner_text() == "이름"
    assert header.get_attribute("aria-sort") == "none"
    button.focus()
    page.keyboard.press("Enter")
    assert header.get_attribute("aria-sort") == "ascending"
    page.keyboard.press("Space")
    assert header.get_attribute("aria-sort") == "descending"


def test_empty_request_button_and_added_row_labels(page):
    login(page)
    page.goto(f"{BASE_URL}/monthly")
    page.fill('#monthly-upload [name="month"]', "2121-02")
    # 앱의 제출 처리 뒤에 이동만 막아 눌린 버튼의 상태를 확인한다.
    page.evaluate(
        "document.getElementById('monthly-upload')"
        ".addEventListener('submit', function (event) { event.preventDefault(); })"
    )
    page.click('button:has-text("빈 요청서 시작")')
    assert page.locator('button[formaction="/monthly/start"]').inner_text() == "검수 목록 준비 중…"
    assert (
        page.locator("#monthly-upload button:not([formaction])").inner_text() == "인식 · 검수하기"
    )

    page.goto(f"{BASE_URL}/monthly")
    page.fill('#monthly-upload [name="month"]', "2121-02")
    page.click('button:has-text("빈 요청서 시작")')
    page.wait_for_selector("#add-row")
    page.click("#add-row")
    rows = page.locator("#rows tbody tr")
    number = rows.count()
    added = rows.last
    assert added.locator('[data-field="name"]').get_attribute("aria-label") == f"{number}행 이름"
    assert (
        added.locator('[data-field="carry"]').get_attribute("aria-label") == f"{number}행 이월 잔액"
    )
    assert added.locator('[data-field="row_id"]').get_attribute("aria-label") is None
    rows.first.locator(".row-del").click()
    assert rows.count() == number - 1
    assert rows.last.locator('[data-field="name"]').get_attribute("aria-label") == (
        f"{number - 1}행 이름"
    )


def test_mobile_navigation_stays_on_one_scrollable_line(page):
    page.set_viewport_size({"width": 375, "height": 740})
    login(page)
    page.goto(f"{BASE_URL}/settings")
    nav = page.locator("nav.nav")
    links = nav.locator("a")
    tops = {round(box["y"]) for box in (links.nth(i).bounding_box() for i in range(links.count()))}
    assert len(tops) == 1
    metrics = page.evaluate(
        """(() => {
          const nav = document.querySelector('nav.nav');
          const header = document.querySelector('.topbar');
          return {
            right: nav.getBoundingClientRect().right,
            viewport: document.documentElement.clientWidth,
            scrolls: nav.scrollWidth > nav.clientWidth,
            header: header.getBoundingClientRect().height,
          };
        })()"""
    )
    assert metrics["right"] <= metrics["viewport"]
    assert metrics["scrolls"]
    assert metrics["header"] < 120
    # 정렬 제목의 화면낭독용 문구가 표 가로 스크롤 밖으로 페이지를 넓히지 않는다.
    page.goto(f"{BASE_URL}/people")
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
