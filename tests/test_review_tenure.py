"""월간 검수는 연결된 기존 인원의 장부 기준 재직 기간을 참고로 보여준다."""

from app.services.tenure import tenure_labels
from tests.factories import make_person
from tests.monthly_helpers import review_fields
from tests.test_legacy_status import legacy_record
from tests.test_monthly_profile_choices import existing, request_row


def test_linked_row_shows_ledger_tenure_with_the_personal_number(auth_client, db):
    person = existing(db)
    legacy_record(db, person, "2025-05", 100)
    legacy_record(db, person, "2026-06", 100)
    expected = tenure_labels(db, [person])[person.id]
    response = request_row(auth_client)
    assert response.status_code == 200
    assert "자동 연결" in response.text
    assert f"재직 기간: {expected}" in response.text
    assert "25/05" in expected


def test_unlinked_row_shows_tenure_only_in_link_candidates(auth_client, db):
    person = existing(db, personal_no="0999")  # 이름만 같아 자동 연결되지 않는다.
    legacy_record(db, person, "2025-05", 100)
    expected = tenure_labels(db, [person])[person.id]
    response = request_row(auth_client)
    assert "연결 확인" in response.text
    assert "재직 기간:" not in response.text
    assert f"재직 {expected}</option>" in response.text


def test_shared_account_and_new_person_show_no_tenure(auth_client, db):
    make_person(db, name="합성공용", personal_no=None, point_no="00000077", account_type="shared")
    response = request_row(
        auth_client,
        name_0="합성공용",
        personal_no_0="",
        point_no_0="00000077",
        account_type_0="shared",
    )
    assert response.status_code == 200
    assert "재직 기간:" not in response.text


def test_tenure_is_display_only_and_not_posted_back(auth_client, db):
    person = existing(db)
    legacy_record(db, person, "2025-05", 100)
    fields = review_fields(request_row(auth_client))
    assert not any("tenure" in key or "재직" in str(value) for key, value in fields.items())
