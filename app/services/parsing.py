"""붙여넣기/엑셀 텍스트에서 요청서 행을 파싱한다.

기본 형식(탭/콤마 구분):
- 7컬럼: 순번, 팀, 이름, 계급, 금액, 개인번호, 비고
- 6컬럼: 첫 컬럼이 숫자면 순번 없는 요청서(순번,팀,이름,계급,금액,개인번호),
  아니면 (팀,이름,계급,금액,개인번호,비고)
- 5컬럼: (팀,이름,계급,금액,개인번호)
"""

import re

from app.services.sync import RequestRow


def _to_int(value: str) -> int:
    value = value.strip().replace(",", "")
    match = re.search(r"\d+", value)
    return int(match.group()) if match else 0


def parse_pasted(text: str) -> list[RequestRow]:
    rows: list[RequestRow] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = [c.strip() for c in re.split(r"\t|,", line) if c.strip()]
        if "\t" in line:
            cols = [c.strip() for c in line.split("\t") if c.strip()]
        else:
            cols = [c.strip() for c in line.split(",") if c.strip()]
        if not cols:
            continue
        team = name = grade = amount = personal_no = note = ""
        if len(cols) == 7 and cols[0].isdigit():
            _, team, name, grade, amount, personal_no, note = cols
        elif len(cols) == 6 and cols[0].isdigit():
            _, team, name, grade, amount, personal_no = cols
        elif len(cols) == 6:
            team, name, grade, amount, personal_no, note = cols
        elif len(cols) == 5:
            team, name, grade, amount, personal_no = cols
        else:
            continue
        if not personal_no or not name:
            continue
        rows.append(
            RequestRow(
                personal_no=personal_no,
                name=name,
                team=team,
                grade=grade,
                amount=_to_int(amount),
                note=note,
            )
        )
    return rows
