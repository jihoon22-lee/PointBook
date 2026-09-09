"""검수 표의 화면 폭·입력 가독성·인원 정보 선택과 작업 위치 보존 회귀."""

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


def assert_input_fully_visible(input_field):
    metrics = input_field.evaluate(
        """input => {
          const style = getComputedStyle(input);
          const context = document.createElement('canvas').getContext('2d');
          context.font = style.font || (style.fontSize + ' ' + style.fontFamily);
          const box = input.getBoundingClientRect();
          const cell = input.closest('td').getBoundingClientRect();
          return {value: input.value, text: context.measureText(input.value).width,
            available: input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
            left: box.left, right: box.right, cellLeft: cell.left, cellRight: cell.right};
        }"""
    )
    assert metrics["text"] <= metrics["available"] + 1, metrics
    assert metrics["left"] >= metrics["cellLeft"] - 1, metrics
    assert metrics["right"] <= metrics["cellRight"] + 1, metrics


def assert_desktop_review_layout(page, team_row):
    for width in (1280, 1920):
        page.set_viewport_size({"width": width, "height": 800})
        dimensions = page.locator("#rows").evaluate(
            """table => {
              const wrap = table.closest('.table-wrap');
              const container = table.closest('main').getBoundingClientRect();
              const note = table.querySelector('th:nth-child(8)').getBoundingClientRect();
              return {width: wrap.getBoundingClientRect().width,
                container: container.width, left: container.left, right: container.right, note: note.width,
                client: wrap.clientWidth, scroll: wrap.scrollWidth,
                document: document.documentElement.scrollWidth, viewport: innerWidth};
            }"""
        )
        assert 1080 < dimensions["container"] <= 1200, dimensions
        assert abs(dimensions["left"] - (width - dimensions["right"])) < 2, dimensions
        assert 170 <= dimensions["note"] <= 195, dimensions
        assert dimensions["scroll"] <= dimensions["client"] + 1, dimensions
        assert dimensions["document"] <= width, dimensions
        point = page.locator('[name="point_no_0"]')
        assert point.input_value() == "00008600"
        for selector in ('[name="point_no_0"]', '[name="amount_0"]', '[name="carry_0"]'):
            assert_input_fully_visible(page.locator(selector))
        name_box = page.locator('[name="name_0"]').bounding_box()
        badge_box = page.locator("#rows .link-state-auto").first.bounding_box()
        assert badge_box["y"] >= name_box["y"] + name_box["height"] - 1
        current = team_row.locator('button[value$=":team:current"]')
        incoming = team_row.locator('button[value$=":team:incoming"]')
        assert current.evaluate("button => button.closest('td').cellIndex") == 3
        assert incoming.evaluate("button => button.closest('td').cellIndex") == 3
        assert "합성1팀" in current.inner_text()
        assert "합성2팀" in incoming.inner_text()
        current_box, incoming_box = current.bounding_box(), incoming.bounding_box()
        assert incoming_box["y"] >= current_box["y"] + current_box["height"] - 1
        assert abs(incoming_box["x"] - current_box["x"]) < 2
        assert abs(incoming_box["width"] - current_box["width"]) < 2
        assert page.locator("#rows tbody > tr").count() == 25


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
        "이월 잔액",
        "충전액",
        "비고",
        "유형",
    ]
    assert page.locator(".link-state-auto").count() == 23
    assert page.locator(".link-state-pending").count() == 2
    assert page.locator(".link-state-new").count() == 0
    assert page.input_value('[name="team_22"]') == "합성2팀"
    assert page.locator('button[value$=":team:incoming"][data-choice-pending="yes"]').count() == 1
    page.fill('[name="amount_0"]', "1,234,567")
    page.fill('[name="carry_0"]', "987,654")
    team_row_id = page.input_value('[name="row_id_22"]')
    team_row = page.locator(f'tr[data-row-id="{team_row_id}"]')
    assert_desktop_review_layout(page, team_row)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.locator("#rows").evaluate(
        "table => table.closest('.table-wrap').scrollWidth > table.closest('.table-wrap').clientWidth"
    )
    # 좁은 화면에서는 표 안의 가로 위치도 실제로 바뀐 상태에서 복원을 검증한다.
    page.set_viewport_size({"width": 800, "height": 720})
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
    assert before["left"] > 0
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
    row.locator('[data-field="team"]').focus()
    row.evaluate("row => row.scrollIntoView({block: 'center'})")
    page.locator("#rows").evaluate("table => table.closest('.table-wrap').scrollLeft = 100")
    before = view_position(page, row_id)
    assert before["left"] > 0
    with page.expect_navigation(wait_until="domcontentloaded"):
        row.locator('button[value$=":team:incoming"]').click()
    assert_position(page, row_id, before, "team")
    row = page.locator(f'tr[data-row-id="{row_id}"]')
    assert row.locator('button[value$=":team:incoming"]').get_attribute("aria-pressed") == "true"
    assert row.locator('button[value$=":team:current"]').get_attribute("aria-pressed") == "false"
    assert row.locator('button[value$=":team:incoming"]').evaluate(
        """button => {
          const current = button.parentElement.querySelector('[value$=":current"]');
          const chosenStyle = getComputedStyle(button), currentStyle = getComputedStyle(current);
          return chosenStyle.backgroundColor !== currentStyle.backgroundColor ||
            chosenStyle.borderColor !== currentStyle.borderColor;
        }"""
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
