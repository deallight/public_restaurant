from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True)
class ParsedExpenseRow:
    row_number: int
    department_name: str
    used_date: str
    place_name: str
    purpose: str
    amount: int
    participants: str = ""
    payment_method: str = ""


def parse_expense_xlsx(payload: bytes) -> list[ParsedExpenseRow]:
    rows = _read_first_sheet(payload)
    header_index, header = _find_header(rows)
    mapping = _header_mapping(header)
    parsed: list[ParsedExpenseRow] = []
    for row_number, row in enumerate(rows[header_index + 1 :], start=1):
        place = _cell(row, mapping["place"])
        amount = _amount(_cell(row, mapping["amount"]))
        used_date = _date(_cell(row, mapping["date"]))
        if not place or _is_placeholder_place(place) or not amount or not used_date:
            continue
        if _normalize(place) in {"계", "합계", "총계"}:
            continue
        parsed.append(
            ParsedExpenseRow(
                row_number=row_number,
                department_name=_cell(row, mapping.get("department", -1)),
                used_date=used_date,
                place_name=place,
                purpose=_cell(row, mapping.get("purpose", -1)),
                amount=amount,
                participants=_cell(row, mapping.get("participants", -1)),
                payment_method=_cell(row, mapping.get("payment_method", -1)),
            )
        )
    return parsed


def _read_first_sheet(payload: bytes) -> list[list[str]]:
    with ZipFile(BytesIO(payload)) as archive:
        shared = _shared_strings(archive)
        sheet_name = _first_sheet_path(archive)
        sheet = ET.fromstring(archive.read(sheet_name))
    rows: list[list[str]] = []
    for row in sheet.findall(".//m:sheetData/m:row", NS):
        values: dict[int, str] = {}
        max_index = -1
        for cell in row.findall("m:c", NS):
            index = _column_index(cell.attrib.get("r", "A"))
            value = _cell_value(cell, shared)
            values[index] = value
            max_index = max(max_index, index)
        rows.append([values.get(index, "") for index in range(max_index + 1)])
    return rows


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [_text(si) for si in root.findall("m:si", NS)]


def _first_sheet_path(archive: ZipFile) -> str:
    names = archive.namelist()
    for name in names:
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
            return name
    raise ValueError("xlsx has no worksheet")


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find("m:v", NS)
    if cell_type == "s" and value is not None:
        return shared[int(value.text or "0")]
    if cell_type == "inlineStr":
        return _text(cell)
    if value is not None:
        return value.text or ""
    return ""


def _column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()) or "A"
    index = 0
    for char in letters:
        index = index * 26 + ord(char.upper()) - 64
    return index - 1


def _text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.findall(".//m:t", NS)).strip()


def _find_header(rows: list[list[str]]) -> tuple[int, list[str]]:
    for index, row in enumerate(rows[:20]):
        normalized = [_normalize(cell) for cell in row]
        if any(cell in {"장소", "사용장소", "집행장소"} for cell in normalized) and any(
            "금액" in cell for cell in normalized
        ):
            return index, row
    raise ValueError("expense header row not found")


def _header_mapping(header: list[str]) -> dict[str, int]:
    normalized = [_normalize(cell) for cell in header]
    mapping = {
        "date": _find_any(normalized, ["일시", "사용일자", "집행일자", "사용일", "일자"]),
        "place": _find_any(normalized, ["장소", "사용장소", "집행장소", "업소명", "상호"]),
        "amount": _find_contains(normalized, "금액"),
    }
    optional = {
        "department": ["사용자", "부서", "담당부서", "집행자"],
        "purpose": ["집행목적", "사용목적", "사용내역", "내용", "목적"],
        "participants": ["대상인원수", "대상인원", "참석인원", "인원"],
        "payment_method": ["결제방법", "사용방법", "지급방법"],
    }
    for key, labels in optional.items():
        try:
            mapping[key] = _find_any(normalized, labels)
        except ValueError:
            mapping[key] = -1
    return mapping


def _find_any(values: list[str], candidates: list[str]) -> int:
    for candidate in candidates:
        if candidate in values:
            return values.index(candidate)
    raise ValueError(f"required column not found: {candidates}")


def _find_contains(values: list[str], needle: str) -> int:
    for index, value in enumerate(values):
        if needle in value:
            return index
    raise ValueError(f"required column not found: {needle}")


def _cell(row: list[str], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _amount(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text)
    if not match:
        return 0
    return int(float(match.group(0).replace(",", "")))


def _date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    compact = re.sub(r"\D", "", text)
    if re.fullmatch(r"20\d{6}", compact):
        year = int(compact[:4])
        month = int(compact[4:6])
        day = int(compact[6:8])
        return datetime(year, month, day).date().isoformat()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        serial = float(text)
        if not 1 <= serial <= 60000:
            return ""
        return (datetime(1899, 12, 30) + timedelta(days=serial)).date().isoformat()
    text = text.replace(".", "-").replace("/", "-")
    match = re.search(r"(20\d{2})-?(\d{1,2})-?(\d{1,2})", text)
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    return datetime(year, month, day).date().isoformat()


def _is_placeholder_place(value: str) -> bool:
    return _normalize(value) in {"-", "ㆍ", "없음", "해당없음", "비공개"}
