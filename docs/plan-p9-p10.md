# PointBook P9~P10 작업 계획서

작성일: 2026-08-06 / 상태: 확정 (승인 완료)

## 개요

| PR | 브랜치 | 목적 |
|---|---|---|
| 9 | `feature/bugfixes` | 기능 버그·누락 수정 (시간대, 파싱 한도, run.sh, 중복 로직, 로그인 strip) |
| 10 | `feature/ui-redesign` | 프론트 디자인 톤 리뉴얼 (모던 블루 + Pretendard + 탑바 개선 + 검수 화면 직전 잔액 표시) |

순서: PR 9 먼저 머지 후 PR 10 진행 (PR 10은 main 최신에서 분기).
각 PR은 "구현 → 자체 검토 → PR(한글) → CI 전체 통과 → squash merge" 워크플로를 따른다.

---

## PR 9 — `feature/bugfixes`

### 배경 (코드 검토에서 발견된 문제)

1. `monthly.py`·`import_excel.py`가 `datetime.now(UTC)` 사용 — 한국(UTC+9) 기준
   월초 새벽 00:00~09:00에 실행하면 "이번 달"이 전 달로 잘못 잡힘
2. `_parse_row_fields`가 `range(100)`으로 100명 하드 한도 — 초과 행 무시
3. `scripts/run.sh`가 포트 점유를 검사하지 않음 — 점유 시 프로세스가 죽고 PID 파일만 남음
4. `monthly.py`의 월별 요약 계산이 `stats.month_summary`와 중복 (설계 원칙 위반)
5. 로그인 username 앞뒤 공백 미처리

### 작업 항목 (파일별)

#### 1) `app/services/dates.py` (신규)

```python
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def current_month() -> str:
    return datetime.now(KST).strftime("%Y-%m")
```

#### 2) `app/routers/monthly.py`

- 로컬 `current_month()`(datetime.now(UTC)) 제거 → `from app.services.dates import current_month`
- `_parse_row_fields`를 while 루프로 변경 (100명 한도 제거):

```python
def _parse_row_fields(form: FormData) -> list[RequestRow]:
    rows: list[RequestRow] = []
    i = 0
    while f"personal_no_{i}" in form:
        personal_no = str(form.get(f"personal_no_{i}", "")).strip()
        name = str(form.get(f"name_{i}", "")).strip()
        if personal_no and name:
            rows.append(RequestRow(...))
        i += 1
    return rows
```

- `monthly_home`/`upload`의 summary 계산 루프 → `stats.month_summary(db, month)` 재사용
- (PR 10에서 직전 잔액 표시를 추가하므로 이번엔 라우터 변경 없음)

#### 3) `scripts/import_excel.py`

- 기본 월 계산을 `current_month()`로 교체 (UTC 제거)

#### 4) `scripts/run.sh`

- 포트 점유 검사 (`ss -tln | grep :$PORT`) — 점유 시 명확한 안내 후 exit 1
- stale PID(프로세스 없음) 감지 시 안내 후 새로 시작

#### 5) `app/routers/auth.py`

- `username = username.strip()` 추가

### 테스트

- `tests/test_dates.py` (신규): 형식 검증 + KST 경계 모킹(1월 1일 00:30 KST → 2026-01)
- `tests/test_monthly.py`: `_parse_row_fields`에 101개 행 — FormData 직접 구성해 101개 파싱 확인
- `tests/test_auth.py`: 공백 포함 username(`" admin "`) 로그인 성공

### 체크리스트

- [ ] dates.py 신규 + monthly.py·import_excel.py 교체
- [ ] _parse_row_fields while 루프
- [ ] run.sh 포트/스테일 PID
- [ ] summary → stats 재사용
- [ ] 로그인 strip
- [ ] 테스트 3종 추가
- [ ] ruff / format / mypy(app scripts) / pytest coverage 85% 통과
- [ ] CI 6 job 통과 → squash merge

---

## PR 10 — `feature/ui-redesign`

### 배경

- 현재 CSS는 기능 위주라 디자인 톤이 평범함 → 모던 블루 톤으로 리뉴얼
- 갤럭시 실기기·Win7(Chrome 109)이 주 사용 환경 — 호환성 유지 (ES5, woff2, 빌드 없음)
- 검수 화면의 이월 잔액 입력이 직전 잔액을 모르면 불편 → 직전 잔액 표시

### 디자인 스펙

**컬러 팔레트**

| 역할 | 값 |
|---|---|
| 탑바 배경 | `#16273f` (다크 네이비) |
| 액센트 | `#2563eb` / 호버 `#1d4ed8` |
| 위험 | `#dc2626` |
| 성공 | `#16a34a` |
| 페이지 배경 | `#f6f7f9` |
| 카드 | `#ffffff` |
| 텍스트 | `#1f2937` / muted `#6b7280` |
| 테두리 | `#e5e7eb` |

**타이포**
- Pretendard woff2 self-host: `app/static/fonts/Pretendard-{Regular,SemiBold,Bold}.subset.woff2`
  (github.com/orioncactus/pretendard v1.3.9 릴리스에서 추출, OFL 1.1 라이센스 — `OFL.txt` 동봉)
- `@font-face` 3개 (400/600/700) + `font-display: swap`
- 폴백: `system-ui, "Malgun Gothic", "Apple SD Gothic Neo", sans-serif`

**컴포넌트**
- 탑바: 다크 네이비, sticky, 현재 페이지 링크 `active` (밑줄/배경 액센트)
- 카드: `border-radius: 14px`, `box-shadow: 0 1px 3px rgba(0,0,0,.06)`
- 요약 카드: 라벨 0.82rem muted + 숫자 1.6rem 700, 우상단 아이콘 자리
- 테이블: 스티키 헤더(`thead th { position: sticky; top: 0 }`), 호버 줄무늬
- 폼: 인풋/셀렉트/텍스트영역 높이 44px(터치 타겟), 포커스 링(`box-shadow: 0 0 0 3px rgba(37,99,235,.15)`)
- 알림: `.alert-success` 클래스 추가, inline 스타일 제거
- 배지: 기존 팀 색상 유지 + 라운드 999px 유지
- 로그인: 중앙 카드 + 배경 그라디언트(`#16273f → #2563eb` 흐림)

**검수 화면 직전 잔액 표시**
- 라우터(`/monthly/upload`): 각 `PersonChange`의 직전 잔액을 `previous_total(db, person_id, month)`로
  계산해 `prev_totals: dict[str, int]`(키: `개인번호|이름`)로 템플릿 전달 (person_id 없으면 0)
- `review.html` placeholder: `직전 잔액: 50,000원` (0이면 `이월 잔액`)

### 작업 파일

| 파일 | 변경 |
|---|---|
| `app/static/fonts/` | Pretendard woff2 3종 + OFL.txt (신규) |
| `app/static/css/style.css` | 전면 재작성 |
| `app/templates/base.html` | 폰트 링크, 탑바 active 클래스 |
| `app/templates/monthly.html` | alert-success 클래스 적용 (inline 제거) |
| `app/templates/review.html` | 잔액 placeholder 직전 잔액 |
| `app/routers/monthly.py` | prev_totals 계산·전달 |
| `app/static/js/dashboard.js` | 차트 컬러 팔레트 맞춤 (선택) |

### 시각 검증 절차

1. 로컬 검증용 스크립트 `e2e/tests/screenshots.py` 작성 (구버전 Chromium, playwright 1.30):
   - 로그인 → 월간 처리(붙여넣기 → 검수 → 확정)로 데이터 시드
   - 데스크톱(1280×900): 로그인/홈/인원/팀/월간/검수/대시보드/인원 상세 캡처
   - 모바일(390×844): 홈/인원/검수 캡처
2. `docker compose run`으로 실행 → `e2e/screenshots/`(gitignore)에 저장 → 이미지 확인
3. E2E 5건 재실행으로 회귀 확인

### 체크리스트

- [ ] Pretendard self-host + OFL.txt
- [ ] style.css 리뉴얼 (팔레트/타이포/컴포넌트/반응형/터치 타겟)
- [ ] base.html 폰트·active
- [ ] monthly.html alert-success
- [ ] review.html 직전 잔액 + 라우터 prev_totals + 테스트
- [ ] 스크린샷 8+2장 검증
- [ ] E2E 5건 통과
- [ ] ruff / format / mypy / pytest coverage 85% 통과
- [ ] CI 6 job 통과 → squash merge

---

## 공통 워크플로

1. `git checkout main && git pull -q` → 최신 main에서 `git checkout -b feature/<slug>`
2. 구현 → 로컬 검증 (ruff check / format --check / mypy app scripts / pytest --cov 85%)
3. 자체 검토 (계획 대비 diff 확인)
4. 커밋(Conventional Commits) → push → PR(한글) 생성
5. CI 6 job 통과 확인 (e2e 포함)
6. squash merge → 로컬 브랜치 정리
