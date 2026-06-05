from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from app.pipeline import BusanCityLiveAdapter
from app.xlsx_parser import parse_expense_xlsx


def make_xlsx(date_value: str = "46030", place: str = "올리바") -> bytes:
    shared = [
        "연번",
        "사용자",
        "일시",
        "장소",
        "집행목적",
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
    def __init__(self, raw_dir: Path):
        super().__init__(max_pages=1, max_documents=1, raw_dir=raw_dir)
        self.xlsx = make_xlsx()

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

    def test_xlsx_parser_accepts_yyyymmdd_dates(self) -> None:
        rows = parse_expense_xlsx(make_xlsx(date_value="20260105"))

        self.assertEqual(rows[0].used_date, "2026-01-05")

    def test_xlsx_parser_skips_placeholder_places(self) -> None:
        self.assertEqual(parse_expense_xlsx(make_xlsx(place="-")), [])


if __name__ == "__main__":
    unittest.main()
