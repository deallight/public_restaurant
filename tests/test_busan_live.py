from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from app.pipeline import BusanCityLiveAdapter
from app.xlsx_parser import parse_expense_xlsx


def make_xlsx(
    date_value: str = "46030",
    place: str = "올리바",
    date_header: str = "일시",
    place_header: str = "장소",
    purpose_header: str = "집행목적",
) -> bytes:
    shared = [
        "연번",
        "사용자",
        date_header,
        place_header,
        purpose_header,
        "대상인원수",
        "금액(원)",
        "결제방법",
        "비목",
        "빅데이터과",
        place,
        "업무 간담",
        "신용카드",
        "시책",
    ]
    sheet = f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c></row>
    <row r="2">
      <c r="A2" t="s"><v>0</v></c><c r="B2" t="s"><v>1</v></c>
      <c r="C2" t="s"><v>2</v></c><c r="D2" t="s"><v>3</v></c>
      <c r="E2" t="s"><v>4</v></c><c r="F2" t="s"><v>5</v></c>
      <c r="G2" t="s"><v>6</v></c><c r="H2" t="s"><v>7</v></c>
      <c r="I2" t="s"><v>8</v></c>
    </row>
    <row r="3">
      <c r="A3"><v>1</v></c><c r="B3" t="s"><v>9</v></c>
      <c r="C3"><v>{date_value}</v></c><c r="D3" t="s"><v>10</v></c>
      <c r="E3" t="s"><v>11</v></c><c r="F3"><v>11</v></c>
      <c r="G3"><v>220000</v></c><c r="H3" t="s"><v>12</v></c>
      <c r="I3" t="s"><v>13</v></c>
    </row>
  </sheetData>
</worksheet>"""
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8"?><sst '
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{item}</t></si>" for item in shared)
        + "</sst>"
    )
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    with ZipFile(tmp.name, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    payload = Path(tmp.name).read_bytes()
    Path(tmp.name).unlink()
    return payload


class FakeLiveAdapter(BusanCityLiveAdapter):
    def __init__(
        self,
        raw_dir: Path,
        date_value: str = "46030",
        start_date: str = "",
        end_date: str = "",
    ):
        super().__init__(
            max_pages=1,
            max_documents=1,
            start_date=start_date,
            end_date=end_date,
            raw_dir=raw_dir,
        )
        self.xlsx = make_xlsx(date_value=date_value)

    def _get_text(self, url: str) -> str:
        if "view" in url:
            return """
            <h4 class="form-data-subject">2026년 제1분기 업무추진비 집행내역(빅데이터과)</h4>
            <dt><span>공표일</span></dt><dd>2026-05-27</dd>
            <dt><span>담당부서</span></dt><dd>본청 &gt; 빅데이터과</dd>
            <a href="/comm/getFile?srvcId=MEDIA&amp;upperNo=1&amp;fileTy=MEDIA&amp;fileNo=1">무시.pdf</a>
            <a href="/comm/getFile?srvcId=OPENGOV&amp;upperNo=21506&amp;fileTy=ATTACH&amp;fileNo=1">업무추진비.xlsx (13 KB)</a>
            """
        return """
        <table>
          <tr><td>10533</td><td><a href="/ghopen12/view?schCommand=Expense&amp;schIndx=21506&amp;curPage=1&amp;">2026년 제1분기 업무추진비 집행내역(빅데이터과)</a></td><td>디지털경제실 &gt; 빅데이터과</td><td>2026-05-27</td></tr>
        </table>
        """

    def _get_bytes(self, url: str, referer: str | None = None) -> bytes:
        return self.xlsx


class AttachmentTimeoutAdapter(FakeLiveAdapter):
    def _get_bytes(self, url: str, referer: str | None = None) -> bytes:
        if "/comm/getFile" in url:
            raise TimeoutError("attachment timed out")
        return FakeLiveAdapter._get_text(self, url).encode("utf-8")


class DateBoundedTargetAdapter(BusanCityLiveAdapter):
    def __init__(self) -> None:
        super().__init__(
            max_pages=500,
            max_documents=10000,
            start_date="2026-01-01",
            end_date="2026-06-20",
        )
        self.requested_pages: list[int] = []

    def _get_text(self, url: str) -> str:
        page = int(url.rsplit("=", 1)[-1])
        self.requested_pages.append(page)
        return str(page)

    def _parse_list(self, html: str, list_url: str) -> list[dict[str, str]]:
        page = int(html)
        if page == 1:
            return [
                {
                    "url": "https://www.busan.go.kr/ghopen12/view/1",
                    "title": "2026년 2분기",
                    "department": "기획담당관",
                    "published_at": "2026-06-10",
                },
                {
                    "url": "https://www.busan.go.kr/ghopen12/view/2",
                    "title": "2026년 1분기",
                    "department": "청년정책과",
                    "published_at": "2026-03-31",
                },
            ]
        if page == 2:
            return [
                {
                    "url": "https://www.busan.go.kr/ghopen12/view/3",
                    "title": "2025년 4분기",
                    "department": "기획담당관",
                    "published_at": "2025-12-31",
                }
            ]
        raise AssertionError("start date boundary should stop additional page scans")


class BusanLiveAdapterTests(unittest.TestCase):
    def test_live_adapter_discovers_opengov_attachment_and_extracts_xlsx_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = FakeLiveAdapter(Path(tmp))
            documents = adapter.discover()
            rows = adapter.extract(documents[0])

        self.assertEqual(len(documents), 1)
        self.assertEqual(len(documents[0].attachments), 1)
        self.assertEqual(rows[0].department_name, "빅데이터과")
        self.assertEqual(rows[0].used_date, "2026-01-08")
        self.assertEqual(rows[0].place_name, "올리바")
        self.assertEqual(rows[0].amount, 220000)

    def test_live_adapter_keeps_document_when_attachment_download_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AttachmentTimeoutAdapter(Path(tmp))
            documents = adapter.discover()

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].attachments, ())

    def test_live_adapter_filters_rows_by_used_date_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = FakeLiveAdapter(
                Path(tmp),
                date_value="20260108",
                start_date="2026-01-01",
                end_date="2026-06-13",
            )
            documents = adapter.discover()
            rows = adapter.extract(documents[0])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].used_date, "2026-01-08")

    def test_live_adapter_skips_rows_outside_used_date_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = FakeLiveAdapter(
                Path(tmp),
                date_value="20251231",
                start_date="2026-01-01",
                end_date="2026-06-13",
            )
            documents = adapter.discover()
            rows = adapter.extract(documents[0])

        self.assertEqual(rows, [])

    def test_live_adapter_stops_scanning_after_start_date_boundary(self) -> None:
        adapter = DateBoundedTargetAdapter()

        targets = adapter.discover_targets()

        self.assertEqual([target.published_at for target in targets], ["2026-06-10", "2026-03-31"])
        self.assertEqual(adapter.requested_pages, [1, 2])

    def test_xlsx_parser_accepts_yyyymmdd_dates(self) -> None:
        rows = parse_expense_xlsx(make_xlsx(date_value="20260105"))

        self.assertEqual(rows[0].used_date, "2026-01-05")

    def test_xlsx_parser_accepts_busan_alias_headers(self) -> None:
        rows = parse_expense_xlsx(
            make_xlsx(
                date_value="46024",
                place="해도",
                date_header="날짜",
                place_header="장소(대상)",
                purpose_header="집  행  내  용",
            )
        )

        self.assertEqual(rows[0].used_date, "2026-01-02")
        self.assertEqual(rows[0].place_name, "해도")
        self.assertEqual(rows[0].purpose, "업무 간담")

    def test_xlsx_parser_accepts_html_table_spreadsheet(self) -> None:
        payload = """
        <html><body><table>
          <tr><td>연번</td><td>날짜</td><td>장소(대상)</td><td>집 행 내 용</td><td>금액(원)</td></tr>
          <tr><td>1</td><td>2026.01.05.</td><td>해도</td><td>직원 격려</td><td>76,000</td></tr>
        </table></body></html>
        """.encode("utf-8")

        rows = parse_expense_xlsx(payload)

        self.assertEqual(rows[0].used_date, "2026-01-05")
        self.assertEqual(rows[0].place_name, "해도")
        self.assertEqual(rows[0].amount, 76000)

    def test_xlsx_parser_skips_placeholder_places(self) -> None:
        self.assertEqual(parse_expense_xlsx(make_xlsx(place="-")), [])


if __name__ == "__main__":
    unittest.main()
