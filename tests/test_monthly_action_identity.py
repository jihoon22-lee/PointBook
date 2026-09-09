"""번호 재사용 뒤 과거 삽입을 다른 인원의 월간 확정으로 집계하지 않는다."""

from app.services import review, stats
from app.services.sync import ACTION_INACTIVE_KEPT, SyncAnalysis
from tests.factories import make_person
from tests.monthly_helpers import reviewed_confirm
from tests.test_legacy_status import legacy_record
from tests.test_profiles import _apply, _edit_form, form_values


def test_reused_point_number_does_not_attach_another_person_monthly_action(
    auth_client, db, monkeypatch
):
    missing = make_person(db, "101", "합성누락", status="inactive")
    attending = make_person(db, "102", "합성재직")
    for person in (missing, attending):
        legacy_record(db, person, "2025-01", 100)
        person.current_amount = 100
    db.commit()
    # 기존 비재직을 관측하지 않던 이전 버전 확정 자료의 호환성을 재현한다.
    analyze = review.analyze

    def legacy_analysis(db, rows):
        return SyncAnalysis(
            [c for c in analyze(db, rows).changes if c.action != ACTION_INACTIVE_KEPT]
        )

    with monkeypatch.context() as previous_version:
        previous_version.setattr(review, "analyze", legacy_analysis)
        confirmed = reviewed_confirm(
            auth_client,
            {
                "month": "2025-02",
                "point_no_0": attending.point_no,
                "personal_no_0": attending.personal_no,
                "name_0": attending.name,
                "team_0": "",
                "grade_0": attending.grade,
                "amount_0": "100",
                "carry_0": "100",
            },
            follow_redirects=False,
        )
    assert confirmed.status_code == 303
    db.expire_all()
    # 인원 편집의 검토·영향 확인을 거쳐 이전 번호를 다른 인원이 사용하게 한다.
    for person, point_no in ((attending, "00000902"), (missing, "00000102")):
        path = f"/people/{person.id}/edit"
        values = _edit_form(auth_client, person, point_no=point_no)
        preview = auth_client.post(path, data=values)
        assert preview.status_code == 200
        assert _apply(auth_client, path, values, preview, confirm_identity="yes").status_code == 303
        db.expire_all()
    before_insert = stats.report_cutoff(db)
    path = f"/ledger/correct/{missing.id}"
    values = {
        **form_values(auth_client.get(f"{path}?month=2025-02")),
        "carry": "100",
        "amount": "0",
        "reason": "기존 장부 누락 월 대조",
    }
    preview = auth_client.post(path + "/preview", data=values)
    assert preview.status_code == 200
    result = auth_client.post(path + "/apply", data=form_values(preview), follow_redirects=False)
    assert result.status_code == 303
    db.expire_all()
    for scope in ("observed", "as_of"):
        report = stats.report(db, "2025-02", scope=scope)
        assert (report.summary.active_count, report.summary.deactivated_count) == (1, 1)
        assert report.summary.workforce_count == 2
        assert report.summary.total_balance == 300
        assert {row.person_id: row.deactivated for row in report.rows} == {
            missing.id: True,
            attending.id: False,
        }
        trend = stats.trend(db, scope=scope)[-1]
        assert (trend.active_count, trend.deactivated_count) == (1, 1)
    original = stats.report(db, "2025-02", operation_id=before_insert)
    assert (original.summary.active_count, original.summary.deactivated_count) == (1, 0)
    assert original.summary.total_balance == 200
    assert missing.status == "inactive" and attending.status == "active"
