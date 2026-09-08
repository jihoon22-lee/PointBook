"""실제 템플릿 파일→검수·초안·확정→동일 개인 보고서 다운로드."""

import io
import zipfile
from xml.etree import ElementTree as ET

from conftest import BASE_URL, login

MULTILINE_NOTE = "\n=문자열 <비고>\n둘째 줄\n"


def fill_template(data):
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for item in source.infolist():
            content = source.read(item)
            if item.filename == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(content)
                rows = root.find("{" + namespace + "}sheetData")
                for existing in list(rows):
                    if existing.attrib.get("r") == "5":
                        rows.remove(existing)
                row = ET.SubElement(rows, "{" + namespace + "}row", {"r": "5"})
                for column, value in zip(
                    "ABCDEFGHI",
                    [
                        "1",
                        "person",
                        "합성팀",
                        "E2E엑셀",
                        "",
                        "150",
                        "950",
                        MULTILINE_NOTE,
                        "50",
                    ],
                ):
                    cell = ET.SubElement(
                        row, "{" + namespace + "}c", {"r": column + "5", "t": "inlineStr"}
                    )
                    inline = ET.SubElement(cell, "{" + namespace + "}is")
                    ET.SubElement(inline, "{" + namespace + "}t").text = value
                rows.remove(row)
                rows.insert(4, row)
                content = ET.tostring(root, encoding="utf-8")
            target.writestr(item.filename, content)
    return output.getvalue()


def test_standard_excel_and_report_download(page):
    login(page)
    template = page.context.request.get(f"{BASE_URL}/monthly/template.xlsx?month=2100-01")
    assert template.ok
    data = fill_template(template.body())
    page.goto(f"{BASE_URL}/monthly")
    page.fill('input[name="month"]', "2100-01")
    page.set_input_files(
        'input[name="file"]',
        {
            "name": "synthetic-request.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "buffer": data,
        },
    )
    page.click('#monthly-upload button[type="submit"]')
    page.wait_for_selector("text=요청서 검수")
    assert page.input_value('input[name="point_no_0"]') == ""
    assert page.locator("button.confirm-monthly").is_disabled()
    # 신규 인원의 외부 발급 번호를 관리자가 검수에서 입력한다.
    page.fill('input[name="point_no_0"]', "00000950")
    assert page.input_value('input[name="point_no_0"]') == "00000950"
    note = page.locator('[name="note_0"]')
    assert note.input_value() == MULTILINE_NOTE
    edited_note = MULTILINE_NOTE + "추가 확인"
    note.fill(edited_note)
    page.wait_for_function(
        "document.getElementById('draft-status').textContent.indexOf('저장 완료') >= 0"
    )
    page.reload()
    assert page.input_value('[name="note_0"]') == edited_note
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.click('button[formaction="/monthly/review"]')
    assert page.input_value('[name="note_0"]') == edited_note
    for carry in page.locator('input[name^="deactivated_carry_"]').all():
        carry.fill("0")
    acknowledgement = page.locator('[name="ack_warnings"]')
    if acknowledgement.count():
        acknowledgement.check()
    page.click("button.confirm-monthly")
    page.wait_for_selector("text=처리가 완료되었습니다")
    page.goto(f"{BASE_URL}/people")
    page.click('a:has-text("E2E엑셀")')
    page.click('a:has-text("개인 보고서")')
    assert "200원" in page.text_content("body")
    with page.expect_download() as download:
        page.click('a:has-text("이 조회 결과 Excel 다운로드")')
    artifact = download.value
    assert artifact.suggested_filename.endswith(".xlsx")
    with zipfile.ZipFile(artifact.path()) as workbook:
        content = workbook.read("xl/worksheets/sheet2.xml").decode()
        assert "00000950" in content
        assert "<f>" not in content
        namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        values = [node.text for node in ET.fromstring(content).findall(".//s:t", namespace)]
        assert edited_note in values


def test_numberless_request_links_existing_account_and_resumes(page):
    login(page)
    page.goto(f"{BASE_URL}/monthly")
    page.fill('input[name="month"]', "2100-02")
    page.fill(
        'textarea[name="pasted"]',
        "팀\t이름\t계급\t충전액\t개인번호\n합성팀\tE2E엑셀\t소방사\t40\t950",
    )
    page.click('#monthly-upload button[type="submit"]')
    page.wait_for_selector('select[name="link_person_0"]')
    assert page.locator("button.confirm-monthly").is_disabled()
    assert page.input_value('[name="point_no_0"]') == ""
    page.fill('[name="carry_0"]', "999")
    page.select_option('select[name="link_person_0"]', index=1)
    # 자동 저장과 연결 submit이 겹쳐도 선택한 행/대상이 전달되어야 한다.
    page.click('button[formaction="/monthly/link"]')
    page.wait_for_function("document.querySelector('[name=point_no_0]').value === '00000950'")
    assert page.input_value('[name="point_no_0"]') == "00000950"
    assert page.input_value('[name="carry_0"]') == ""
    assert page.input_value('[name="amount_0"]') == "40"
    page.reload()
    assert page.input_value('[name="point_no_0"]') == "00000950"
    page.fill('[name="carry_0"]', "80")
    for carry in page.locator('input[name^="deactivated_carry_"]').all():
        carry.fill("0")
    acknowledgement = page.locator('[name="ack_warnings"]')
    if acknowledgement.count():
        acknowledgement.check()
    page.click("button.confirm-monthly")
    page.wait_for_selector("text=처리가 완료되었습니다")
    page.goto(f"{BASE_URL}/people")
    assert page.locator('a:has-text("E2E엑셀")').count() == 1
    page.click('a:has-text("E2E엑셀")')
    assert "120원" in page.text_content("body")


def test_ie_guidance_uses_no_app_form(browser):
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 6.1; Trident/7.0; rv:11.0) like Gecko"
    )
    try:
        page = context.new_page()
        response = page.goto(f"{BASE_URL}/login")
        assert response.status == 426
        assert "다른 브라우저로 열어 주세요" in page.text_content("body")
        assert page.locator("form").count() == 0
    finally:
        context.close()
