from sqlalchemy import select

from app.models import Person
from app.services.sync import (
    ACTION_DEACTIVATED,
    ACTION_KEPT,
    ACTION_NEW,
    ACTION_RETURNED,
    RequestRow,
    analyze,
    apply_analysis,
)
from tests.factories import make_person, make_team


def _row(personal_no="1001", name="홍길동", team="1팀", grade="소방위", amount=50000):
    return RequestRow(personal_no=personal_no, name=name, team=team, grade=grade, amount=amount)


def test_analyze_new_person(client, db):
    analysis = analyze(db, [_row()])
    assert [c.action for c in analysis.changes] == [ACTION_NEW]


def test_analyze_new_person_does_not_write(client, db):
    analyze(db, [_row()])
    assert db.scalar(select(Person)) is None


def test_analyze_kept_person(client, db):
    make_person(db, "1001", "홍길동")
    analysis = analyze(db, [_row()])
    assert [c.action for c in analysis.changes] == [ACTION_KEPT]


def test_analyze_deactivates_missing_person(client, db):
    make_person(db, "1001", "홍길동")
    analysis = analyze(db, [_row("2002", "김철수")])
    actions = {c.action for c in analysis.changes}
    assert ACTION_DEACTIVATED in actions
    assert ACTION_NEW in actions
    deactivated = next(c for c in analysis.changes if c.action == ACTION_DEACTIVATED)
    assert deactivated.personal_no == "1001"


def test_analyze_returned_person(client, db):
    make_person(db, "1001", "홍길동", status="inactive")
    analysis = analyze(db, [_row()])
    assert [c.action for c in analysis.changes] == [ACTION_RETURNED]


def test_analyze_stays_inactive_when_absent(client, db):
    make_person(db, "1001", "홍길동", status="inactive")
    analysis = analyze(db, [_row("2002", "김철수")])
    actions = [c.action for c in analysis.changes]
    assert ACTION_DEACTIVATED not in actions
    assert ACTION_NEW in actions


def test_analyze_team_change_detected(client, db):
    team_a = make_team(db, "1팀")
    make_person(db, "1001", "홍길동", team=team_a)
    analysis = analyze(db, [_row(team="2팀")])
    change = next(c for c in analysis.changes if c.action == ACTION_KEPT)
    assert change.team_changed is True


def test_analyze_no_team_change(client, db):
    team_a = make_team(db, "1팀")
    make_person(db, "1001", "홍길동", team=team_a)
    analysis = analyze(db, [_row(team="1팀")])
    change = next(c for c in analysis.changes if c.action == ACTION_KEPT)
    assert change.team_changed is False


def test_analyze_duplicate_rows_count_once(client, db):
    analysis = analyze(db, [_row(), _row()])
    assert len(analysis.changes) == 1


def test_apply_new_person_creates(client, db):
    analysis = analyze(db, [_row()])
    apply_analysis(db, analysis)
    db.commit()
    person = db.scalar(select(Person))
    assert person is not None
    assert person.status == "active"
    assert person.team.name == "1팀"


def test_apply_deactivates(client, db):
    person = make_person(db, "1001", "홍길동")
    analysis = analyze(db, [_row("2002", "김철수")])
    apply_analysis(db, analysis)
    db.commit()
    db.refresh(person)
    assert person.status == "inactive"


def test_apply_returns_to_active(client, db):
    person = make_person(db, "1001", "홍길동", status="inactive")
    analysis = analyze(db, [_row()])
    apply_analysis(db, analysis)
    db.commit()
    db.refresh(person)
    assert person.status == "active"


def test_apply_team_change(client, db):
    team_a = make_team(db, "1팀")
    person = make_person(db, "1001", "홍길동", team=team_a)
    analysis = analyze(db, [_row(team="2팀")])
    apply_analysis(db, analysis)
    db.commit()
    db.refresh(person)
    assert person.team.name == "2팀"


def test_apply_keeps_grade_default(client, db):
    person = make_person(db, "1001", "홍길동", grade="소방경")
    analysis = analyze(db, [_row(grade="")])
    apply_analysis(db, analysis)
    db.commit()
    db.refresh(person)
    assert person.grade == "소방경"


def test_apply_new_person_no_team(client, db):
    analysis = analyze(db, [_row(team="")])
    apply_analysis(db, analysis)
    db.commit()
    person = db.scalar(select(Person))
    assert person is not None
    assert person.team_id is None


def test_apply_new_person_sets_current_amount(client, db):
    analysis = analyze(db, [_row(amount=40000)])
    apply_analysis(db, analysis)
    db.commit()
    person = db.scalar(select(Person))
    assert person.current_amount == 40000
    assert person.current_carry_balance == 0
