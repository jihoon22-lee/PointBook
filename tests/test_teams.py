from tests.factories import make_person, make_team


def test_teams_page_empty(auth_client):
    resp = auth_client.get("/teams")
    assert resp.status_code == 200
    assert "등록된 팀이 없습니다" in resp.text


def test_create_team(auth_client, db):
    resp = auth_client.post(
        "/teams", data={"name": "구조대", "color": "#c0392b"}, follow_redirects=False
    )
    assert resp.status_code == 303
    resp = auth_client.get("/teams")
    assert "구조대" in resp.text


def test_create_team_duplicate(auth_client, db):
    make_team(db, "구조대")
    resp = auth_client.post("/teams", data={"name": "구조대", "color": "#c0392b"})
    assert resp.status_code == 400
    assert "이미 존재합니다" in resp.text


def test_create_team_empty_name(auth_client, db):
    resp = auth_client.post("/teams", data={"name": "  ", "color": "#c0392b"})
    assert resp.status_code == 400
    assert "팀 이름을 입력" in resp.text


def test_delete_team_empty(auth_client, db):
    team = make_team(db, "행정지원팀")
    resp = auth_client.post(f"/teams/{team.id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert "행정지원팀" not in auth_client.get("/teams").text


def test_delete_team_releases_persons(auth_client, db):
    team = make_team(db, "구조대")
    person = make_person(db, team=team)
    resp = auth_client.post(f"/teams/{team.id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    db.refresh(person)
    assert person.team_id is None


def test_delete_team_missing(auth_client, db):
    resp = auth_client.post("/teams/9999/delete", follow_redirects=False)
    assert resp.status_code == 303


def test_teams_requires_login(client):
    resp = client.get("/teams", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_team_detail_shows_members(auth_client, db):
    team = make_team(db, "구조대", "#c0392b")
    make_person(db, "1001", "홍길동", team=team)
    make_person(db, "1002", "김철수", team=team, status="inactive")
    resp = auth_client.get(f"/teams/{team.id}")
    assert resp.status_code == 200
    assert "홍길동" in resp.text
    assert "김철수" in resp.text
    assert "재직" in resp.text
    assert "비재직" in resp.text
    assert 'href="/people/' in resp.text


def test_team_detail_empty(auth_client, db):
    team = make_team(db, "빈팀")
    resp = auth_client.get(f"/teams/{team.id}")
    assert resp.status_code == 200
    assert "소속 인원이 없습니다" in resp.text


def test_team_detail_missing(auth_client):
    resp = auth_client.get("/teams/9999", follow_redirects=False)
    assert resp.status_code == 303


def test_team_list_links_to_detail(auth_client, db):
    team = make_team(db, "구조대")
    resp = auth_client.get("/teams")
    assert f'href="/teams/{team.id}"' in resp.text


def test_team_list_separates_total_active_and_inactive_counts(auth_client, db):
    team = make_team(db, "구조대")
    make_person(db, "1001", "재직자", team=team)
    make_person(db, "1002", "비재직자", team=team, status="inactive")

    resp = auth_client.get("/teams")

    assert resp.status_code == 200
    assert "전체 2명" in resp.text
    assert "재직 1명" in resp.text
    assert "비재직 1명" in resp.text


def test_team_list_counts_only_person_accounts(auth_client, db):
    team = make_team(db, "구조대")
    make_person(db, "1001", "재직자", team=team)
    make_person(
        db,
        personal_no="",
        point_no="00009999",
        name="구조대 공용",
        team=team,
        account_type="shared",
    )

    resp = auth_client.get("/teams")

    assert resp.status_code == 200
    assert "전체 1명" in resp.text
    assert "재직 1명" in resp.text


def test_team_detail_orders_active_members_before_inactive_members(auth_client, db):
    team = make_team(db, "구조대")
    make_person(db, "1001", "가비재직", team=team, status="inactive")
    make_person(db, "1002", "하재직", team=team)

    resp = auth_client.get(f"/teams/{team.id}")

    assert resp.status_code == 200
    assert resp.text.index("하재직") < resp.text.index("가비재직")


def test_team_detail_shows_only_person_accounts(auth_client, db):
    team = make_team(db, "구조대")
    make_person(db, "1001", "홍길동", team=team)
    make_person(
        db,
        personal_no="",
        point_no="00009999",
        name="구조대 공용",
        team=team,
        account_type="shared",
    )

    resp = auth_client.get(f"/teams/{team.id}")

    assert resp.status_code == 200
    assert "홍길동" in resp.text
    assert "구조대 공용" not in resp.text


def test_team_detail_shows_each_members_current_total_balance(auth_client, db):
    team = make_team(db, "구조대")
    person = make_person(db, "1001", "홍길동", team=team)
    person.current_carry_balance = 12_345
    person.current_amount = 6_789
    db.commit()

    resp = auth_client.get(f"/teams/{team.id}")

    assert resp.status_code == 200
    assert "총잔액" in resp.text
    assert "19,134원" in resp.text


def test_team_create_uses_free_color_picker(auth_client, db):
    resp = auth_client.get("/teams")
    assert 'type="color"' in resp.text
    assert 'name="color"' in resp.text
    assert 'type="radio" name="color"' not in resp.text


def test_team_list_shows_color_editor_for_existing_team(auth_client, db):
    team = make_team(db, "1팀", "#c0392b")

    resp = auth_client.get("/teams")

    assert resp.status_code == 200
    assert f'action="/teams/{team.id}/color"' in resp.text
    assert 'type="color"' in resp.text
    assert 'value="#c0392b"' in resp.text
    assert "색상은 선택하면 자동으로 저장됩니다" in resp.text
    assert 'aria-label="1팀 색상 저장"' not in resp.text
    assert "/static/js/teams.js" in resp.text


def test_existing_team_color_can_be_changed_without_releasing_members(auth_client, db):
    team = make_team(db, "1팀", "#c0392b")
    person = make_person(db, team=team)

    resp = auth_client.post(
        f"/teams/{team.id}/color",
        data={"color": "#2563eb"},
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/teams"
    db.refresh(team)
    db.refresh(person)
    assert team.color == "#2563eb"
    assert person.team_id == team.id


def test_existing_team_color_rejects_invalid_value(auth_client, db):
    team = make_team(db, "1팀", "#c0392b")

    resp = auth_client.post(
        f"/teams/{team.id}/color",
        data={"color": "red; background-image: url(evil)"},
    )

    assert resp.status_code == 400
    assert "올바른 색상" in resp.text
    db.refresh(team)
    assert team.color == "#c0392b"


def test_create_team_rejects_invalid_color(auth_client, db):
    resp = auth_client.post(
        "/teams",
        data={"name": "위험팀", "color": "red; background-image: url(evil)"},
    )

    assert resp.status_code == 400
    assert "올바른 색상" in resp.text
    assert "위험팀" not in auth_client.get("/teams").text


def test_team_badges_choose_readable_text_for_light_mid_and_dark_colors(auth_client, db):
    light = make_team(db, "밝은팀", "#ffffff")
    mid = make_team(db, "중간팀", "#808080")
    dark = make_team(db, "어두운팀", "#000000")

    light_page = auth_client.get(f"/teams/{light.id}")
    mid_page = auth_client.get(f"/teams/{mid.id}")
    dark_page = auth_client.get(f"/teams/{dark.id}")

    assert 'style="background: #ffffff; color: #000000"' in light_page.text
    assert 'style="background: #808080; color: #000000"' in mid_page.text
    assert 'style="background: #000000; color: #ffffff"' in dark_page.text


def test_team_detail_sorting_applies_to_name_and_total_balance(auth_client, db):
    team = make_team(db, "구조대")
    lower = make_person(db, "1001", "가인원", team=team)
    lower.current_carry_balance = 100
    higher = make_person(db, "1002", "하인원", team=team)
    higher.current_carry_balance = 500
    db.commit()

    by_name_desc = auth_client.get(f"/teams/{team.id}?sort=name&dir=desc")
    by_total_asc = auth_client.get(f"/teams/{team.id}?sort=total&dir=asc")

    assert by_name_desc.text.index("하인원") < by_name_desc.text.index("가인원")
    assert by_total_asc.text.index("가인원") < by_total_asc.text.index("하인원")


def test_team_detail_all_information_headers_are_sortable(auth_client, db):
    team = make_team(db, "구조대")
    make_person(db, team=team)

    resp = auth_client.get(f"/teams/{team.id}")

    assert resp.status_code == 200
    for sort_key in ("name", "point_no", "personal_no", "grade", "status", "total"):
        assert f"sort={sort_key}" in resp.text
    assert "정렬 중: 오름차순" in resp.text


def test_team_current_balance_totals_include_inactive_and_exclude_shared(auth_client, db):
    from app.routers.teams import _team_summaries

    team = make_team(db, "합성팀")
    empty = make_team(db, "빈팀")
    active = make_person(db, "1001", team=team)
    inactive = make_person(db, "1002", team=team, status="inactive")
    shared = make_person(db, "1003", team=team, account_type="shared")
    unassigned = make_person(db, "1004")
    active.current_carry_balance, active.current_amount = 12_345, 6_789
    inactive.current_carry_balance, inactive.current_amount = 30_000, 0
    shared.current_carry_balance = 999_999
    unassigned.current_carry_balance = 888_888
    db.commit()
    summaries = {row.team.id: row for row in _team_summaries(db)}
    summary = summaries[team.id]
    assert (summary.active_count, summary.inactive_count, summary.total_count) == (1, 1, 2)
    assert (summary.active_balance, summary.inactive_balance, summary.total_balance) == (
        19_134,
        30_000,
        49_134,
    )
    assert summaries[empty.id].total_count == summaries[empty.id].total_balance == 0
    listing = auth_client.get("/teams").text
    detail = auth_client.get(f"/teams/{team.id}").text
    assert all(label in listing for label in ("재직 총 잔액", "전체 총 잔액", "등록 팀 합계"))
    assert "49,134원" in listing and "49,134원" in detail and "30,000원" in detail
    assert "999,999원" not in listing and "888,888원" not in listing


def test_team_color_json_response_and_failure_preserve_saved_color(auth_client, db):
    team = make_team(db, "합성팀")
    url = f"/teams/{team.id}/color"
    headers = {"Accept": "application/json"}
    saved = auth_client.post(url, data={"color": "#ABCDEF"}, headers=headers)
    assert saved.status_code == 200 and saved.json() == {"color": "#abcdef"}
    invalid = auth_client.post(url, data={"color": "invalid"}, headers=headers)
    assert invalid.status_code == 400 and "error" in invalid.json()
    db.refresh(team)
    assert team.color == "#abcdef"


def test_team_sum_preserves_large_imported_integer_balances_without_sql_overflow(auth_client, db):
    from app.routers.teams import _team_summaries

    team = make_team(db, "합성 큰 잔액")
    for number in (1, 2):
        person = make_person(db, str(number), team=team)
        person.current_carry_balance = 2**62 + number
    inactive = make_person(db, "3", team=team, status="inactive")
    inactive.current_carry_balance = 7
    db.commit()
    summary = _team_summaries(db)[0]
    assert summary.active_balance == 2**63 + 3
    assert summary.total_balance == 2**63 + 10
    for url in ("/teams", f"/teams/{team.id}"):
        response = auth_client.get(url)
        assert response.status_code == 200
        assert f"{2**63 + 10:,}원" in response.text
