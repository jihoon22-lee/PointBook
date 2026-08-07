from sqlalchemy import create_engine, text

from app.db import migrate


def test_migrate_adds_current_balance_columns(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE people (id INTEGER PRIMARY KEY, personal_no VARCHAR(50),"
                " name VARCHAR(50), grade VARCHAR(50), status VARCHAR(20),"
                " team_id INTEGER, created_at DATETIME)"
            )
        )
        conn.execute(text("INSERT INTO people (personal_no, name) VALUES ('1', '기존인원')"))

    migrate(eng)

    with eng.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(people)"))}
        assert "current_carry_balance" in cols
        assert "current_amount" in cols
        row = conn.execute(
            text("SELECT current_carry_balance, current_amount FROM people")
        ).fetchone()
        assert tuple(row) == (0, 0)


def test_migrate_is_idempotent(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE people (id INTEGER PRIMARY KEY, personal_no VARCHAR(50),"
                " name VARCHAR(50), grade VARCHAR(50), status VARCHAR(20), team_id INTEGER,"
                " current_carry_balance INTEGER NOT NULL DEFAULT 0,"
                " current_amount INTEGER NOT NULL DEFAULT 0, created_at DATETIME)"
            )
        )
    migrate(eng)
    migrate(eng)
    with eng.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(people)"))}
        assert "current_carry_balance" in cols
