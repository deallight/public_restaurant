from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


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
    rows = _read_rows(payload)
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


def _read_rows(payload: bytes) -> list[list[str]]:
    if _looks_like_html(payload):
        return _read_html_tables(payload)
    if payload.startswith(b"\x9b DRMONE") or b"DRMONE" in payload[:64]:
        raise ValueError("unsupported encrypted DRM file")
    if payload.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError("unsupported legacy xls binary file")
    try:
        return _read_first_sheet(payload)
    except BadZipFile as exc:
        raise ValueError("unsupported spreadsheet file: not xlsx/html") from exc


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


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._in_cell = False
        self._current_row: list[str] | None = None
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._current_row = []
        elif tag.lower() in {"td", "th"} and self._current_row is not None:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._in_cell and self._current_row is not None:
            self._current_row.append(re.sub(r"\s+", " ", "".join(self._current_cell)).strip())
            self._current_cell = []
            self._in_cell = False
        elif tag.lower() == "tr" and self._current_row is not None:
            if any(cell.strip() for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def _looks_like_html(payload: bytes) -> bool:
    head = payload[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<table" in head


def _read_html_tables(payload: bytes) -> list[list[str]]:
    text = _decode_text(payload)
    parser = _TableParser()
    parser.feed(text)
    if not parser.rows:
        raise ValueError("html table not found")
    return parser.rows


def _decode_text(payload: bytes) -> str:
    for encoding in ["utf-8-sig", "cp949", "euc-kr", "utf-16"]:
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


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
    for index, row in enumerate(rows[:50]):
        normalized = [_normalize(cell) for cell in row]
        if any(_is_place_header(cell) for cell in normalized) and any(_is_amount_header(cell) for cell in normalized):
            return index, row
    raise ValueError("expense header row not found")


def _header_mapping(header: list[str]) -> dict[str, int]:
    normalized = [_normalize(cell) for cell in header]
    mapping = {
        "date": _find_label(
            normalized,
            ["일시", "날짜", "사용일자", "집행일자", "사용일", "일자", "집행일", "지출일자", "사용일시"],
        ),
        "place": _find_label(
            normalized,
            ["장소", "사용장소", "집행장소", "업소명", "상호", "상호명", "사용처", "지급처", "업체명"],
        ),
        "amount": _find_amount(normalized),
    }
    optional = {
        "department": ["사용자", "부서", "담당부서", "집행자"],
        "purpose": ["집행목적", "사용목적", "사용내역", "집행내용", "내용", "목적", "내역"],
        "participants": ["대상인원수", "대상인원", "참석인원", "인원"],
        "payment_method": ["결제방법", "사용방법", "지급방법"],
    }
    for key, labels in optional.items():
        try:
            mapping[key] = _find_label(normalized, labels)
        except ValueError:
            mapping[key] = -1
    return mapping


def _is_place_header(value: str) -> bool:
    return value in {"장소", "사용장소", "집행장소", "업소명", "상호", "상호명", "사용처", "지급처", "업체명"} or (
        "장소" in value and "목적" not in value
    )


def _is_amount_header(value: str) -> bool:
    return any(token in value for token in ["금액", "집행액", "지출액", "사용액"])


def _find_label(values: list[str], candidates: list[str]) -> int:
    for candidate in candidates:
        if candidate in values:
            return values.index(candidate)
    for index, value in enumerate(values):
        if any(candidate in value for candidate in candidates):
            return index
    raise ValueError(f"required column not found: {candidates}")


def _find_amount(values: list[str]) -> int:
    for index, value in enumerate(values):
        if _is_amount_header(value):
            return index
    raise ValueError("required column not found: amount")


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
