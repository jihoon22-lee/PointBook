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
    assert "소속 인원과 공용계정이 없습니다" in resp.text


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


def test_team_detail_shows_person_and_shared_accounts(auth_client, db):
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
    assert "구조대 공용" in resp.text


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


def test_team_current_balance_totals_include_inactive_and_shared(auth_client, db):
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
        1_049_133,
    )
    assert summaries[empty.id].total_count == summaries[empty.id].total_balance == 0
    listing = auth_client.get("/teams").text
    detail = auth_client.get(f"/teams/{team.id}").text
    assert all(label in listing for label in ("재직 총 잔액", "전체 총 잔액", "등록 팀 합계"))
    assert "1,049,133원" in listing and "1,049,133원" in detail and "30,000원" in detail
    assert "999,999원" in listing and "888,888원" not in listing


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
    shared = make_person(db, "4", team=team, account_type="shared")
    shared.current_carry_balance = 2**62
    shared.current_amount = 13
    db.commit()
    summary = _team_summaries(db)[0]
    assert summary.active_balance == 2**63 + 3
    assert summary.shared_balance == 2**62 + 13
    assert summary.total_balance == 3 * 2**62 + 23
    for url in ("/teams", f"/teams/{team.id}"):
        response = auth_client.get(url)
        assert response.status_code == 200
        assert f"{3 * 2**62 + 23:,}원" in response.text


def test_shared_only_team_keeps_person_counts_zero_and_shows_account(auth_client, db):
    from app.routers.teams import _team_summaries

    team = make_team(db, "합성 공용팀")
    shared = make_person(db, "", "합성 공용", team=team, point_no="99000001", account_type="shared")
    shared.personal_no = None
    shared.grade = ""
    shared.current_carry_balance, shared.current_amount = 12_300, 700
    db.commit()

    summary = _team_summaries(db)[0]
    assert (summary.total_count, summary.active_count, summary.inactive_count) == (0, 0, 0)
    assert (summary.shared_count, summary.shared_balance, summary.total_balance) == (
        1,
        13_000,
        13_000,
    )
    listing = auth_client.get("/teams")
    detail = auth_client.get(f"/teams/{team.id}")
    assert "공용 1개" in listing.text and "13,000원" in listing.text
    assert "일반 0명 · 공용 1개" in detail.text
    assert f'href="/people/{shared.id}"' in detail.text
    assert ">공용</span>" in detail.text
    assert detail.text.count("<td>-</td>") == 3  # 개인번호·계급·재직 기간
    assert "account_type=all&scope=as_of" in detail.text
    db.refresh(shared)
    assert shared.status == "active"


def test_registered_team_totals_keep_unassigned_shared_accounts_separate(auth_client, db):
    first = make_team(db, "가 합성팀")
    second = make_team(db, "나 합성팀")
    empty = make_team(db, "다 빈팀")
    for number, team, kind, status, balance in [
        (1, first, "person", "active", 100),
        (2, first, "person", "inactive", 200),
        (3, first, "shared", "active", 300),
        (4, second, "shared", "active", 400),
        (5, second, "shared", "active", 500),
        (6, None, "person", "active", 600),
        (7, None, "shared", "active", 700),
    ]:
        person = make_person(db, str(number), team=team, account_type=kind, status=status)
        person.current_carry_balance = balance
    db.commit()

    listing = auth_client.get("/teams")
    assert listing.context["totals"] == {
        "total_count": 2,
        "active_count": 1,
        "active_balance": 100,
        "shared_count": 3,
        "shared_balance": 1_200,
        "total_balance": 1_500,
    }
    assert "팀이 지정되지 않은 일반 인원·공용계정은 등록 팀 합계에서 제외합니다" in listing.text
    assert "1,500원" in listing.text
    detail = auth_client.get(f"/teams/{empty.id}")
    summary = detail.context["summary"]
    assert (summary.total_count, summary.shared_count, summary.total_balance) == (0, 0, 0)
    assert "소속 인원과 공용계정이 없습니다" in detail.text


def test_shared_members_join_sorting_with_null_fields_and_stable_ties(auth_client, db):
    team = make_team(db, "합성 정렬팀")
    person = make_person(db, "4101", "나 일반", team=team, grade="소방사")
    inactive = make_person(db, "4102", "다 비재직", team=team, status="inactive")
    shared = make_person(db, "", "가 공용", team=team, point_no="99000002", account_type="shared")
    shared.personal_no = None
    shared.grade = ""
    person.current_carry_balance = 300
    inactive.current_carry_balance = 200
    shared.current_carry_balance = 100
    db.commit()
    cases = [
        ("name", "asc", [shared, person, inactive]),
        ("name", "desc", [inactive, person, shared]),
        ("personal_no", "asc", [person, inactive, shared]),
        ("personal_no", "desc", [inactive, person, shared]),
        ("grade", "asc", [shared, person, inactive]),
        ("status", "asc", [shared, person, inactive]),
        ("total", "asc", [shared, inactive, person]),
        ("total", "desc", [person, inactive, shared]),
    ]
    for key, direction, expected in cases:
        response = auth_client.get(f"/teams/{team.id}?sort={key}&dir={direction}")
        assert [p.id for p in response.context["members"]] == [p.id for p in expected]
    shared.current_carry_balance = person.current_carry_balance
    db.commit()
    response = auth_client.get(f"/teams/{team.id}?sort=total&dir=desc")
    assert [p.id for p in response.context["members"]] == [shared.id, person.id, inactive.id]


def test_shared_team_move_and_delete_keep_account_and_balance(auth_client, db):
    from tests.test_profiles import _submit_profile

    first = make_team(db, "가 합성팀")
    second = make_team(db, "나 합성팀")
    shared = make_person(
        db, "", "합성 공용", team=first, point_no="99000003", account_type="shared"
    )
    shared.personal_no = None
    shared.grade = ""
    shared.current_carry_balance, shared.current_amount = 2_000, 500
    db.commit()
    moved = _submit_profile(
        auth_client,
        f"/people/{shared.id}/edit",
        data={
            "point_no": shared.point_no,
            "personal_no": "",
            "name": shared.name,
            "grade": "",
            "team_id": str(second.id),
            "account_type": "shared",
            "status": "active",
        },
        follow_redirects=False,
    )
    assert moved.status_code == 303
    old_detail = auth_client.get(f"/teams/{first.id}")
    new_detail = auth_client.get(f"/teams/{second.id}")
    assert old_detail.context["summary"].shared_count == 0
    assert old_detail.context["summary"].total_balance == 0
    assert new_detail.context["summary"].shared_count == 1
    assert new_detail.context["summary"].total_balance == 2_500
    assert auth_client.post(f"/teams/{second.id}/delete", follow_redirects=False).status_code == 303
    db.refresh(shared)
    assert shared.team_id is None and shared.status == "active"
    assert (shared.current_carry_balance, shared.current_amount) == (2_000, 500)
    assert auth_client.get("/teams").context["totals"]["total_balance"] == 0
    assert auth_client.get(f"/people/{shared.id}").status_code == 200
