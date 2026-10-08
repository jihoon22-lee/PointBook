"""팀 소속 공용계정의 단위·잔액·상세 연결을 합성 자료로 확인한다."""

from conftest import BASE_URL, login


def test_team_shared_balance_and_detail_on_mobile(page):
    login(page)
    team_name = "공용표시합성팀"
    page.goto(f"{BASE_URL}/teams")
    page.fill('[name="name"]', team_name)
    page.click('button:has-text("추가")')
    team_link = page.locator("tbody a", has_text=team_name)
    team_path = team_link.get_attribute("href")
    team_id = team_path.rsplit("/", 1)[-1]

    page.goto(f"{BASE_URL}/people/new")
    page.select_option('[name="account_type"]', "shared")
    page.fill('[name="point_no"]', "99000101")
    page.fill('[name="name"]', "공용표시합성계정")
    page.select_option('[name="team_id"]', team_id)
    page.fill('[name="carry_balance"]', "12000")
    page.fill('[name="amount"]', "3000")
    page.click('button[name="intent"][value="preview"]')
    page.click('button[name="intent"][value="apply"]')
    page.wait_for_url("**/people/*")
    shared_path = page.url.replace(BASE_URL, "")

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{BASE_URL}/teams")
    row = page.locator("tbody tr", has=page.locator("a", has_text=team_name))
    assert "전체 0명" in row.inner_text()
    assert "공용 1개" in row.inner_text()
    assert row.locator("td").nth(6).inner_text() == "15,000원"
    assert row.locator("td").nth(7).inner_text() == "15,000원"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    row.locator("a").click()
    assert "일반 0명 · 공용 1개" in page.locator("h2").inner_text()
    member = page.locator("tbody tr", has_text="공용표시합성계정")
    assert member.locator(".badge").inner_text() == "공용"
    assert member.locator("td").all_text_contents()[2:4] == ["-", "-"]
    assert member.locator("td").nth(5).inner_text() == "-"
    assert member.locator("a").get_attribute("href") == shared_path
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate(
        """() => Array.from(document.querySelectorAll('body *')).filter(el => {
          const box = el.getBoundingClientRect();
          return box.right > innerWidth && !el.closest('.table-wrap');
        }).map(el => [el.tagName, el.className, el.getBoundingClientRect().right])"""
    )
    report = page.locator('a:has-text("팀 보고서 · Excel")').get_attribute("href")
    assert "account_type=all&scope=as_of" in report
    member.locator("a").click()
    page.wait_for_url(f"**{shared_path}")
    assert "공용표시합성계정" in page.locator("h1").inner_text()
