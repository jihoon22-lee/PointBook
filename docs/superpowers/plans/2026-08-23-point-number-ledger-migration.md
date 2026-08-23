# Point Number Ledger Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make normalized point numbers the sole business identifier, support shared accounts and signed net usage, and safely import the approved 28-month cumulative ledger into the local SQLite database.

**Architecture:** Keep integer primary/foreign keys and the existing `usage` column, but centralize point-number validation and switch all identity lookups to `point_no`. Add a dedicated cumulative-ledger parser and a dry-run-first CLI whose apply path backs up the database and commits all imported domain data in one transaction. Use a safe Alembic placeholder only for generic legacy upgrades; the approved empty-history test accounts are removed through an explicit CLI flag during the local import.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, SQLite, Alembic, openpyxl, Jinja2, pytest, Ruff, mypy, Docker Playwright E2E.

**Spec:** `docs/superpowers/specs/2026-08-23-point-number-ledger-migration-design.md`

## Global Constraints

- Work only on `feature/point-number-ledger-migration`; never push directly to `main`.
- Never commit the real workbook, `data/*.db`, backups, screenshots, or reports containing real identities.
- Store `point_no` as exactly eight digits without separators; display it as `0000 0000`.
- `point_no` is mandatory and unique for both `person` and `shared` accounts.
- `personal_no` may repeat and may be NULL only for shared accounts.
- Preserve signed `usage`; negative values are valid net increases.
- Keep Chrome 109-compatible JavaScript and the current server-rendered frontend.
- Dry-run is the default; apply requires `--apply`, a successful backup, and every expected invariant.
- Replace existing unmatched people only with `--replace-empty-history-people`, and only when both snapshot and balance-record counts are zero.
- Include the user-provided `scripts/deploy.sh` after verifying it contains no credentials.

---

### Task 1: Point-number domain model and safe schema migration

**Files:**
- Create: `app/services/identifiers.py`
- Create: `migrations/versions/b7d9f2a1c4e6_point_number_accounts.py`
- Modify: `app/models.py`
- Modify: `app/template_utils.py`
- Modify: `tests/factories.py`
- Create: `tests/test_identifiers.py`
- Modify: `tests/test_migrate.py`

**Interfaces:**
- Produces: `normalize_point_no(value: str) -> str`
- Produces: `format_point_no(value: str | None) -> str`
- Produces: `is_legacy_point_no(value: str | None) -> bool`
- Produces: `Person.point_no: str` and `Person.account_type: str`
- Consumes: existing Alembic batch migration pattern and SQLAlchemy models

- [ ] **Step 1: Add failing identifier tests**

```python
@pytest.mark.parametrize("raw", ["0000 0001", "0000-0001", "00000001"])
def test_normalize_point_no(raw):
    assert normalize_point_no(raw) == "00000001"


@pytest.mark.parametrize("raw", ["", "1234567", "123456789", "abcd1234"])
def test_normalize_point_no_rejects_invalid(raw):
    with pytest.raises(ValueError, match="포인트번호"):
        normalize_point_no(raw)


def test_format_point_no():
    assert format_point_no("00000001") == "0000 0001"
```

- [ ] **Step 2: Run the focused tests and verify the missing module failure**

Run: `env -u TEMP -u TMP uv run pytest tests/test_identifiers.py -q`

Expected: FAIL because `app.services.identifiers` does not exist.

- [ ] **Step 3: Implement identifier helpers**

```python
POINT_NO_RE = re.compile(r"^\d{8}$")


def normalize_point_no(value: str) -> str:
    normalized = re.sub(r"[\s-]", "", value.strip())
    if not POINT_NO_RE.fullmatch(normalized):
        raise ValueError("포인트번호는 숫자 8자리여야 합니다.")
    return normalized


def format_point_no(value: str | None) -> str:
    if value is None:
        return "-"
    return f"{value[:4]} {value[4:]}" if POINT_NO_RE.fullmatch(value) else "미전환"


def is_legacy_point_no(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"L\d{7}", value))
```

- [ ] **Step 4: Add failing model and migration tests**

Test four exact cases in `tests/test_migrate.py`: an existing two-person DB gets
two distinct values matching `L\d{7}`; a fresh DB exposes non-null unique
`point_no`; two people with the same `(personal_no, name)` but different point
numbers commit successfully; and a shared account with `personal_no=None`
commits successfully. Assert the upgraded columns include non-null `point_no`, non-null
`account_type`, nullable `personal_no`, no `uq_person_key`, and a unique point-number index.

- [ ] **Step 5: Implement model and Alembic changes**

Add to `Person`:

```python
point_no: Mapped[str] = mapped_column(String(8), unique=True, index=True)
personal_no: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
account_type: Mapped[str] = mapped_column(String(20), default="person", index=True)
```

The migration must add nullable `point_no`, populate existing rows with `L`
plus a seven-digit zero-padded ID, then batch-recreate the table with `point_no`
non-null and unique, `personal_no` nullable, `account_type` defaulting to
`person`, and without `uq_person_key`.

- [ ] **Step 6: Register the display filter and update synthetic factories**

Register `format_point_no` as Jinja filter `point_no` and extend the factory with
`point_no: str = "00000001"` and `account_type: str = "person"` parameters so
every synthetic account is valid.

- [ ] **Step 7: Run focused validation**

Run:

```bash
env -u TEMP -u TMP uv run pytest tests/test_identifiers.py tests/test_migrate.py -q
uv run ruff check app/services/identifiers.py app/models.py tests/test_identifiers.py tests/test_migrate.py
uv run mypy app/services/identifiers.py app/models.py
```

Expected: all pass.

- [ ] **Step 8: Commit the schema unit**

```bash
git add app/models.py app/services/identifiers.py app/template_utils.py migrations/versions/b7d9f2a1c4e6_point_number_accounts.py tests/factories.py tests/test_identifiers.py tests/test_migrate.py
git commit -m "feat(identity): 포인트번호 계정 식별자 도입"
```

---

### Task 2: Point-number-based request parsing, AI extraction, and synchronization

**Files:**
- Modify: `app/services/sync.py`
- Modify: `app/services/parsing.py`
- Modify: `app/services/excel_import.py`
- Modify: `app/ai/gemini.py`
- Modify: `app/ai/mock.py`
- Modify: `tests/test_sync.py`
- Modify: `tests/test_monthly.py`
- Modify: `tests/test_excel_import.py`
- Modify: `tests/test_gemini.py`

**Interfaces:**
- Consumes: `normalize_point_no()` and `Person.point_no`
- Produces: `RequestRow(point_no, personal_no, name, team, grade, amount, note)`
- Produces: `PersonChange.point_no` and profile-change flags
- Produces: `analyze(db, rows)` that raises `ValueError` for normalized duplicates

- [ ] **Step 1: Change test builders to require point numbers and write new failing cases**

Add exact cases that assert: a matching point number with changed name and
personal number returns `ACTION_KEPT` for the existing `person_id`; normalized
duplicate numbers raise `ValueError`; an absent shared account produces no
`ACTION_DEACTIVATED`; pasted input without a point number raises `ValueError`;
the one-month Excel importer rejects a workbook without a point-number column;
and Gemini output without `point_no` raises `ValueError`. Use only synthetic
point numbers such as `00000001` and `00000002`.

- [ ] **Step 2: Run the focused suites and verify failures**

Run:

```bash
env -u TEMP -u TMP uv run pytest tests/test_sync.py tests/test_monthly.py tests/test_excel_import.py tests/test_gemini.py -q
```

Expected: FAIL where `point_no` is not yet part of the request contract.

- [ ] **Step 3: Change sync identity and profile-update behavior**

Use these dataclass fields:

```python
@dataclass
class RequestRow:
    point_no: str
    personal_no: str
    name: str
    team: str = ""
    grade: str = ""
    amount: int = 0
    note: str = ""
```

Normalize once per row, reject duplicates, look up only by `Person.point_no`,
update name/personal number/team/grade for an existing point number, and limit
deactivation candidates to `account_type == "person"`.

- [ ] **Step 4: Require point numbers in pasted and AI data**

Support these tabular layouts:

```text
순번 팀 이름 계급 금액 개인번호 포인트번호 비고
팀 이름 계급 금액 개인번호 포인트번호 비고
팀 이름 계급 금액 개인번호 포인트번호
```

Update the Gemini prompt and JSON contract to include `point_no` as a string.
Raise a user-safe `ValueError` when any parsed/AI row lacks or has an invalid
point number.

- [ ] **Step 5: Update the existing one-month Excel importer**

Add `point_no` header synonyms, require the field, normalize it, create/query by
`Person.point_no`, and retain its empty-DB-only behavior.

- [ ] **Step 6: Run focused tests and static checks**

Run:

```bash
env -u TEMP -u TMP uv run pytest tests/test_sync.py tests/test_monthly.py tests/test_excel_import.py tests/test_gemini.py -q
uv run ruff check app/services/sync.py app/services/parsing.py app/services/excel_import.py app/ai tests
uv run mypy app/services/sync.py app/services/parsing.py app/services/excel_import.py app/ai
```

Expected: all pass.

- [ ] **Step 7: Commit the request-flow unit**

```bash
git add app/services/sync.py app/services/parsing.py app/services/excel_import.py app/ai tests/test_sync.py tests/test_monthly.py tests/test_excel_import.py tests/test_gemini.py
git commit -m "feat(sync): 포인트번호 기준 월간 동기화 전환"
```

---

### Task 3: Monthly confirmation, people UI, and signed net-usage reporting

**Files:**
- Modify: `app/services/balance.py`
- Modify: `app/services/stats.py`
- Modify: `app/routers/monthly.py`
- Modify: `app/routers/people.py`
- Modify: `app/templates/monthly.html`
- Modify: `app/templates/review.html`
- Modify: `app/templates/person_form.html`
- Modify: `app/templates/people.html`
- Modify: `app/templates/person_detail.html`
- Modify: `app/templates/team_detail.html`
- Modify: `app/templates/dashboard.html`
- Modify: `app/static/js/dashboard.js`
- Modify: `tests/test_balance.py`
- Modify: `tests/test_people.py`
- Modify: `tests/test_dashboard.py`
- Modify: `tests/test_teams.py`

**Interfaces:**
- Consumes: point-number `RequestRow`, `Person.point_no`, `format_point_no`
- Produces: `compute_usage(prev_total, carry_balance)` with signed return value
- Produces: carry form keys such as `carry_00000001`
- Produces: account-type-aware person CRUD and statistics

- [ ] **Step 1: Write failing signed-usage and UI tests**

Start with the executable signed-usage assertion:

```python
def test_compute_usage_preserves_negative_value():
    assert compute_usage(3000, 5000) == -2000
```

Then add route assertions that post `carry_00000001`, reject a duplicate after
normalizing `0000-0001`, allow a shared form with an empty personal number,
find `0000 0001` through either formatted or compact search, sum `-2000` in the
dashboard, and exclude the shared account from the person count.

- [ ] **Step 2: Run focused tests and verify failures**

Run:

```bash
env -u TEMP -u TMP uv run pytest tests/test_balance.py tests/test_people.py tests/test_dashboard.py tests/test_teams.py tests/test_monthly.py -q
```

Expected: FAIL on clamped usage, old carry keys, and missing account fields.

- [ ] **Step 3: Implement signed usage and point-number confirmation keys**

Change `compute_usage()` to:

```python
return prev_total - carry_balance
```

Parse and render carries by normalized point number. After applying sync,
resolve accounts only through `Person.point_no` and sum amounts by `row.point_no`.

- [ ] **Step 4: Implement account-aware people CRUD and search**

Require and normalize `point_no` on create/edit. Require `personal_no` for
`person`; store NULL and empty grade for `shared`. Search name, personal number,
raw normalized point number, and the user's separator-stripped query. Show and
edit account type without deleting any account.

- [ ] **Step 5: Update templates and dashboard copy**

Add formatted point numbers and account type to all required tables/forms.
Replace `사용` labels with `순사용`; include exactly this guidance:

```text
양수는 포인트 순감소, 음수는 적립·이전 등으로 인한 순증가입니다.
```

Keep JavaScript ES5-compatible and update chart labels only; do not introduce a
frontend build.

- [ ] **Step 6: Run focused tests and static checks**

Run:

```bash
env -u TEMP -u TMP uv run pytest tests/test_balance.py tests/test_people.py tests/test_dashboard.py tests/test_teams.py tests/test_monthly.py -q
uv run ruff check app tests
uv run mypy app
```

Expected: all pass.

- [ ] **Step 7: Commit the application/UI unit**

```bash
git add app/services/balance.py app/services/stats.py app/routers app/templates app/static/js/dashboard.js tests
git commit -m "feat(ui): 포인트번호와 순사용 화면 반영"
```

---

### Task 4: Dedicated cumulative-ledger parser and invariant report

**Files:**
- Create: `app/services/ledger_import.py`
- Create: `tests/test_ledger_import.py`

**Interfaces:**
- Consumes: `normalize_point_no()` and openpyxl workbooks
- Produces: `parse_ledger(path: Path) -> LedgerData`
- Produces: `validate_expected_totals(data: LedgerData) -> None`
- Produces: masked `LedgerSummary` with counts, month aggregates, warning coordinates

- [ ] **Step 1: Build a synthetic workbook fixture and failing mapping tests**

The fixture must use 74 synthetic people, four synthetic shared accounts, and
28 blocks with the same blank/nonblank structure but no real names or numbers.
Assert the parsed month tuple starts at `2024-05`, ends at `2026-08`, and has
length 28; blank person-months create no record; a returning person's usage is
based on its latest existing record; a carry greater than the previous total
produces a negative value; the six known late payment headers produce warning
coordinates; missing carry or total raises `LedgerImportError`; a total mismatch
raises the same masked error; deliberately huge values in rows 76 and 77 do not
change any aggregate; and the result has four shared accounts with no records.

- [ ] **Step 2: Run the parser tests and verify the missing module failure**

Run: `env -u TEMP -u TMP uv run pytest tests/test_ledger_import.py -q`

Expected: FAIL because `ledger_import` does not exist.

- [ ] **Step 3: Implement immutable source dataclasses and explicit mapping**

Define focused types:

```python
@dataclass(frozen=True)
class LedgerAccount:
    point_no: str
    personal_no: str | None
    name: str
    grade: str
    team_name: str
    account_type: str
    status: str


@dataclass(frozen=True)
class LedgerRecord:
    point_no: str
    month: str
    carry_balance: int
    amount: int
    usage: int
    total: int


@dataclass(frozen=True)
class LedgerWarning:
    code: str
    cell: str


@dataclass(frozen=True)
class LedgerData:
    people: tuple[LedgerAccount, ...]
    shared_accounts: tuple[LedgerAccount, ...]
    records: tuple[LedgerRecord, ...]
    months: tuple[str, ...]
    warnings: tuple[LedgerWarning, ...]
```

Declare all 28 source mappings as constants. Parse only sheet `간식비`, rows
2:75, and shared-account cells CJ:CL. Never read rows 76 or 77 for a result.

- [ ] **Step 4: Implement validations and masked summaries**

Validate A:E headers, account counts, point-number completeness/uniqueness,
carry-header month relationships, required cells, `total=carry+amount`, month
uniqueness, and signed usage. Summary output may include counts, coordinates,
months and totals, but never names or complete point/personal numbers.

- [ ] **Step 5: Run focused validation**

Run:

```bash
env -u TEMP -u TMP uv run pytest tests/test_ledger_import.py -q
uv run ruff check app/services/ledger_import.py tests/test_ledger_import.py
uv run mypy app/services/ledger_import.py
```

Expected: all pass.

- [ ] **Step 6: Commit the parser unit**

```bash
git add app/services/ledger_import.py tests/test_ledger_import.py
git commit -m "feat(import): 누적 장부 파서와 검증 추가"
```

---

### Task 5: Dry-run-first database planner, transactional apply, and CLI

**Files:**
- Modify: `app/services/ledger_import.py`
- Create: `scripts/import_ledger.py`
- Modify: `tests/test_ledger_import.py`
- Create: `tests/test_import_ledger_cli.py`

**Interfaces:**
- Consumes: `LedgerData`, configured SQLAlchemy `Session`, `backup_database()`
- Produces: `analyze_ledger_import(db, data, replace_empty_history_people=False) -> LedgerImportPlan`
- Produces: `apply_ledger_import(db, plan) -> LedgerApplyResult`
- Produces: CLI `--file`, mutually exclusive `--dry-run`/`--apply`, and `--replace-empty-history-people`

- [ ] **Step 1: Add failing dry-run and transaction tests**

Add tests that snapshot table counts before and after dry-run; assert apply
creates the fixture's exact account/month/record counts; assert one exact legacy
match is updated in place; assert two legacy candidates raise a masked error;
assert replacement is refused when either snapshots or records exist; inject an
exception after record creation and assert all counts roll back; assert an
existing month and a second apply are rejected; capture CLI output and assert it
contains `DRY-RUN` but neither the synthetic name nor its full point number; and
mock a failed backup to assert zero writes.

- [ ] **Step 2: Run the focused tests and verify failures**

Run:

```bash
env -u TEMP -u TMP uv run pytest tests/test_ledger_import.py tests/test_import_ledger_cli.py -q
```

Expected: FAIL because DB planning/apply and CLI do not exist.

- [ ] **Step 3: Implement read-only DB planning**

`analyze_ledger_import()` must load current people, snapshot months and record
counts without writes. It returns exact create/update/delete counts and either
a valid plan or raises a masked `LedgerImportError`. Match legacy rows by exact
`(personal_no, name)` only for one-time backfill. Never use that pair in normal
sync.

- [ ] **Step 4: Implement transactional data apply**

Inside one explicit transaction:

```python
if plan.delete_unmatched_empty_history_people:
    db.execute(delete(Person).where(Person.id.in_(plan.delete_person_ids)))
# create/update teams and accounts by point_no
# create 28 MonthlySnapshot rows and 1,392 BalanceRecord rows
# update latest balances and 44/30 statuses
validate_database_invariants(db)
```

Do not commit inside helpers. Let the CLI commit only after every invariant;
rollback on any exception.

- [ ] **Step 5: Implement the CLI and backup gate**

The default invocation prints `DRY-RUN` and the masked summary. `--apply` must
call `backup_database()` first and abort if an existing DB was not backed up.
The replacement flag is accepted only with a valid empty-history plan. Never
print exception objects that may contain a source row's identity.

- [ ] **Step 6: Run focused tests and static checks**

Run:

```bash
env -u TEMP -u TMP uv run pytest tests/test_ledger_import.py tests/test_import_ledger_cli.py -q
uv run ruff check app/services/ledger_import.py scripts/import_ledger.py tests/test_ledger_import.py tests/test_import_ledger_cli.py
uv run mypy app/services/ledger_import.py scripts/import_ledger.py
```

Expected: all pass.

- [ ] **Step 7: Commit the apply/CLI unit**

```bash
git add app/services/ledger_import.py scripts/import_ledger.py tests/test_ledger_import.py tests/test_import_ledger_cli.py
git commit -m "feat(import): dry-run 및 트랜잭션 이관 CLI 추가"
```

---

### Task 6: Documentation, deployment script, and full automated verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/usage-guide.md`
- Modify: `CHANGELOG.md`
- Modify: `AGENTS.md`
- Add: `scripts/deploy.sh`
- Modify: `e2e/tests/test_flow.py`
- Modify: `e2e/tests/verify_design.py`

**Interfaces:**
- Consumes: final CLI and user-facing behavior
- Produces: operator documentation and a repository-tracked deployment script

- [ ] **Step 1: Update docs from the verified contracts**

Document point-number identity, normalization, duplicate personal numbers,
shared accounts, signed net usage, the cumulative-ledger dry-run/apply commands,
backup/rollback, placeholder backfill, replacement flag safety, and prohibited
personal-data files.

- [ ] **Step 2: Review and add `scripts/deploy.sh`**

Verify the script has no credentials or local personal paths, uses fast-forward
main updates, installs locked dependencies, backs up `data/pointbook.db`, and
restarts through the repository scripts. Add it without embedding `.env` values.

- [ ] **Step 3: Update E2E expectations**

Keep the existing flow but provide valid synthetic point numbers, verify the
formatted value on review/person pages, and assert the `순사용` label. Do not
add real workbook fixtures or screenshots.

- [ ] **Step 4: Run the full local quality gate**

Run serially:

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy app scripts
env -u TEMP -u TMP uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=85
```

Expected: all checks pass and coverage is at least 85%.

- [ ] **Step 5: Verify migrations on temporary databases**

Run `alembic upgrade head` and `alembic check` with a temporary
`DATABASE_PATH`, then run the migration tests for a fresh DB and an existing
data DB. Never point these checks at `data/pointbook.db`.

- [ ] **Step 6: Run Docker E2E**

Run:

```bash
docker compose -f e2e/compose.yml up --build --abort-on-container-exit --exit-code-from e2e
```

Expected: all E2E checks pass. Tear down only the compose project created by
this command.

- [ ] **Step 7: Scan the branch for private data and review the diff**

Confirm no `.xlsx`, `.db`, backup, real identity, or full point number from the
source workbook appears in tracked files. Review `git diff main...HEAD` and
working-tree changes for correctness.

- [ ] **Step 8: Commit docs, E2E, and deployment**

```bash
git add README.md docs CHANGELOG.md AGENTS.md scripts/deploy.sh e2e
git commit -m "docs: 포인트번호 이관 및 배포 절차 정리"
```

---

### Task 7: Real workbook dry-run and approved local database replacement

**Files:**
- Read only: the 80,221-byte corrected workbook outside the repository
- Modify locally only: `data/pointbook.db`
- Create locally only: `data/backups/*.db`

**Interfaces:**
- Consumes: verified CLI, corrected real workbook, explicit approval to delete the two empty-history test people
- Produces: migrated local database and masked verification evidence

- [ ] **Step 1: Reconfirm the exact targets before stopping anything**

Resolve the workbook hash, copy the verified source to the gitignored local path
`data/import/source-ledger.xlsx`, resolve the DB absolute path, PointBook server
PID/command and current DB counts. Abort if the workbook is not the corrected
`F=5월분` file or if snapshots/records are no longer zero.

- [ ] **Step 2: Run real-data dry-run without changing the DB**

Run the CLI with `--replace-empty-history-people` but without `--apply`. Verify
all exact counts and aggregates from the design spec, plus two planned test-person
deletions. Capture only masked output.

- [ ] **Step 3: Stop only the PointBook process**

Stop the repository-owned Uvicorn process serving `app.main:app` on port 8002.
Do not stop SoolJang containers or any unrelated process. Confirm the PointBook
PID exited before continuing.

- [ ] **Step 4: Create and verify a timestamped backup**

Use `scripts.backup` or the importer backup gate. Verify the backup exists, is
non-empty, and has the pre-migration SQLite integrity check `ok`.

- [ ] **Step 5: Apply the import**

Run:

```bash
uv run python -m scripts.import_ledger --file data/import/source-ledger.xlsx --apply --replace-empty-history-people
```

The local workbook path is supplied at execution time and must never be copied
into tracked files or command logs in the PR.

- [ ] **Step 6: Verify the migrated DB independently**

Run masked SQL/integrity checks for 78 accounts, 74/4 account types, 28 months,
1,392 records, 44/30 person status, 5 negative usages, no NULL/legacy/invalid or
duplicate point numbers, the exact first/final month totals, and `PRAGMA
integrity_check = ok`.

- [ ] **Step 7: Restart PointBook and smoke-test application flows**

Restart through `scripts/run.sh`, then verify login, point-number search, a
person's history, the 2026-08 dashboard, and a monthly review with synthetic
input. Do not expose real names or point numbers in screenshots or logs.

---

### Task 8: Review, PR, CI, and merge

**Files:**
- Review: all branch changes against `main`

**Interfaces:**
- Consumes: complete automated checks and local migration evidence
- Produces: Korean review-ready PR and merged change

- [ ] **Step 1: Run verification-before-completion checks**

Re-run the full quality gate, Alembic checks, E2E, private-data scan, and local
DB invariant query using fresh output. Record Windows 7 and Android real-device
checks as unverified rather than claiming success.

- [ ] **Step 2: Push the feature branch and create a Korean PR**

The PR must explain purpose, schema/API contract, importer safety, files, tests,
actual local DB result, rollback backup, risks, and the real-device verification
boundary. Do not include personal identifiers or local attachment paths.

- [ ] **Step 3: Monitor all CI jobs**

Wait for lint, typecheck, test, migrations, security, secret-scan, E2E, and final
`Quality gate`. Fix failures on the feature branch and repeat until green.

- [ ] **Step 4: Squash merge and verify final state**

Squash merge only after `Quality gate` is green. Verify `main` contains the
merge, the feature branch/PR status is correct, and the local PointBook service
still passes a health/smoke check.
