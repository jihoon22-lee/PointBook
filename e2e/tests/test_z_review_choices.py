"""연결 상태·인원 정보 선택과 하단 행의 작업 위치 보존 회귀."""

from conftest import BASE_URL, choose_incoming_profiles, login


def upload(page, month, pasted):
    page.goto(f"{BASE_URL}/monthly")
    page.fill('[name="month"]', month)
    page.fill('[name="pasted"]', pasted)
    page.click('#monthly-upload button[type="submit"]')
    page.wait_for_selector("#review-form")


def confirm_seed(page):
    for carry in page.locator('input[name^="carry_"],input[name^="deactivated_carry_"]').all():
        carry.fill("0")
    acknowledgement = page.locator('[name="ack_warnings"]')
    if acknowledgement.count():
        acknowledgement.check()
    page.click(".confirm-monthly")
    page.wait_for_selector("text=처리가 완료되었습니다")


def view_position(page, row_id):
    return page.evaluate(
        """id => {
          const row = Array.from(document.querySelectorAll('#rows tbody tr')).find(row => row.dataset.rowId === id);
          return {top: row.getBoundingClientRect().top, y: window.scrollY,
            left: document.querySelector('#rows').closest('.table-wrap').scrollLeft,
            focus: document.activeElement.dataset.field || ''};
        }""",
        row_id,
    )


def assert_position(page, row_id, before, field):
    page.wait_for_function("document.activeElement.getAttribute('data-field') === '" + field + "'")
    after = view_position(page, row_id)
    assert abs(after["top"] - before["top"]) < 3, (before, after)
    assert abs(after["left"] - before["left"]) < 3, (before, after)
    assert after["y"] > 500
    assert page.locator("#row-sort").input_value() == "name"
    assert page.locator("#row-filter").input_value() == "all"
    assert page.evaluate("Object.keys(sessionStorage).length") == 0


def test_link_states_profile_choices_and_lower_row_position(page):
    login(page)
    names = [f"위치합성{i:02d}" for i in range(24)]
    upload(
        page,
        "2101-01",
        "팀\t이름\t계급\t충전액\t개인번호\t포인트번호\n"
        + "\n".join(
            f"합성1팀\t{name}\t소방사\t100\t{8600 + i}\t{8600 + i:08d}"
            for i, name in enumerate(names)
        ),
    )
    confirm_seed(page)
    upload(
        page,
        "2101-02",
        "팀\t이름\t계급\t충전액\t개인번호\n"
        + "\n".join(
            f"{'합성2팀' if i >= 22 else '합성1팀'}\t{name}\t소방사\t40\t{8699 if i == 23 else 8600 + i}"
            for i, name in enumerate(names)
        )
        + "\n합성1팀\t추가합성신규\t소방사\t50\t8700",
    )
    assert page.locator("#rows thead th").all_text_contents()[:9] == [
        "이름",
        "개인번호",
        "포인트번호",
        "팀",
        "계급",
        "충전액",
        "이월잔액",
        "비고",
        "유형",
    ]
    assert page.locator(".link-state-auto").count() == 23
    assert page.locator(".link-state-pending").count() == 2
    assert page.locator(".link-state-new").count() == 0
    assert page.input_value('[name="team_22"]') == "합성2팀"
    assert page.locator('button[value$=":team:incoming"][data-choice-pending="yes"]').count() == 1
    point = page.locator('[name="point_no_0"]')
    assert point.is_visible() and point.input_value() == "00008600"
    page.check("#focus-input")
    assert point.is_visible()
    page.uncheck("#focus-input")
    page.select_option("#row-sort", "name")

    row_id = page.input_value('[name="row_id_23"]')
    row = page.locator(f'tr[data-row-id="{row_id}"]')
    selector = row.locator('[data-field="link_person"]')
    value = selector.locator("option").evaluate_all(
        "options => options.find(option => option.textContent.includes('위치합성23')).value"
    )
    selector.select_option(value)
    row.evaluate("row => row.scrollIntoView({block: 'center'})")
    page.locator("#rows").evaluate("table => table.closest('.table-wrap').scrollLeft = 180")
    row.locator('[data-field="name"]').evaluate("input => input.focus({preventScroll: true})")
    before = view_position(page, row_id)
    with page.expect_navigation(wait_until="domcontentloaded"):
        row.locator('button[formaction="/monthly/link"]').click()
    assert_position(page, row_id, before, "name")
    row = page.locator(f'tr[data-row-id="{row_id}"]')
    assert row.locator(".link-state-manual").count() == 1
    assert row.locator('[data-field="point_no"]').input_value() == "00008623"
    assert row.locator('[data-field="team"]').input_value() == "합성2팀"
    assert row.locator('[data-field="personal_no"]').input_value() == "8699"
    assert row.locator('[data-field="carry"]').input_value() == ""
    assert row.locator('button[data-choice-pending="yes"]').count() == 4

    # 가로로 스크롤한 하단 행도 선택 전 입력 위치로 돌아온다.
    row.locator('[data-field="personal_no"]').focus()
    row.evaluate("row => row.scrollIntoView({block: 'center'})")
    page.locator("#rows").evaluate("table => table.closest('.table-wrap').scrollLeft = 100")
    before = view_position(page, row_id)
    with page.expect_navigation(wait_until="domcontentloaded"):
        row.locator('button[value$=":personal_no:incoming"]').click()
    assert_position(page, row_id, before, "personal_no")
    row = page.locator(f'tr[data-row-id="{row_id}"]')
    assert (
        row.locator('button[value$=":personal_no:incoming"]').get_attribute("aria-pressed")
        == "true"
    )
    choose_incoming_profiles(page)
    assert page.locator('button[data-choice-pending="yes"]').count() == 0
    assert row.locator('[data-field="team"]').input_value() == "합성2팀"

    new_row_id = page.input_value('[name="row_id_24"]')
    new_row = page.locator(f'tr[data-row-id="{new_row_id}"]')
    with page.expect_response(lambda response: response.url.endswith("/monthly/new")):
        new_row.locator('button[formaction="/monthly/new"]').click()
    new_row.locator(".link-state-new").wait_for()
    assert new_row.locator(".link-state-new").count() == 1
    assert new_row.locator('[data-field="point_no"]').input_value() == ""
    assert page.locator(".confirm-monthly").is_disabled()
    new_row.locator('[data-field="point_no"]').fill("00008700")
    with page.expect_response(lambda response: response.url.endswith("/monthly/review")):
        page.click('button[formaction="/monthly/review"]')
    page.wait_for_function("document.getElementById('review-token').value !== ''")
    assert new_row.locator(".link-state-new").count() == 1
    assert new_row.locator('[data-field="point_no"]').input_value() == "00008700"
    assert page.locator(".confirm-monthly").is_enabled()
