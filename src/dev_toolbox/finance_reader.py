from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from pathlib import Path


DELIMITER_CANDIDATES = [
    ("\t", "Tab"),
    ("|", "竖线|"),
    (",", "逗号,"),
    (";", "分号;"),
    ("^", "脱字符^"),
    ("~", "波浪线~"),
    ("\x01", "SOH(0x01)"),
]

ENCODING_CANDIDATES = ("utf-8-sig", "gb18030", "utf-16", "utf-16-le", "utf-16-be", "big5", "latin-1")
MAX_PREVIEW_ROWS = 100_000


@dataclass
class FinanceParseResult:
    path: str
    encoding: str
    format_kind: str
    confidence: str
    delimiter: str
    delimiter_name: str
    record_length: str
    total_rows: int
    displayed_rows: int
    headers: list[str]
    rows: list[tuple[str, ...]]
    raw_preview: str
    details: list[tuple[str, str]]
    warnings: list[str]


def parse_finance_file(
    path: str | Path,
    *,
    mode: str = "自动识别",
    delimiter: str = "",
    widths_text: str = "",
    has_header: bool = False,
    preview_limit: int = MAX_PREVIEW_ROWS,
) -> FinanceParseResult:
    file_path = Path(path)
    data = file_path.read_bytes()
    if file_path.suffix.lower() == ".dbf":
        return _parse_dbf(file_path, data, preview_limit)
    text, encoding = _decode_data(data)
    lines = text.splitlines()
    sample = [line for line in lines[:500] if line != ""]
    warnings: list[str] = []

    widths = _parse_widths(widths_text)
    detected_delimiter, delimiter_name, delimiter_score = _detect_delimiter(sample)
    fixed_score, record_length = _detect_fixed_width(sample)
    selected_mode = mode
    if selected_mode == "自动识别":
        if widths:
            selected_mode = "字段定长"
        elif detected_delimiter and delimiter_score >= 0.72:
            selected_mode = "固定分隔符"
        elif fixed_score >= 0.72:
            selected_mode = "字段定长"
        else:
            selected_mode = "原始文本"

    if selected_mode == "固定分隔符":
        if delimiter:
            detected_delimiter = _decode_delimiter(delimiter)
            delimiter_name = _delimiter_display(detected_delimiter)
        if not detected_delimiter:
            selected_mode = "原始文本"
            warnings.append("未能识别稳定分隔符，已按原始文本展示。")

    if selected_mode == "固定分隔符" and detected_delimiter:
        headers, rows = _parse_delimited(lines, detected_delimiter, has_header, preview_limit)
        confidence = _confidence(delimiter_score)
        format_kind = "固定分隔符文件"
    elif selected_mode == "字段定长":
        inferred_widths = widths or _infer_fixed_widths(sample)
        if not inferred_widths:
            headers = ["原始记录"]
            rows = [(line,) for line in lines[:preview_limit]]
            warnings.append("已识别为定长/无分隔符文件；如需字段级拆分，请输入字段宽度，如：6,8,12,20。")
        else:
            headers, rows = _parse_fixed_width(lines, inferred_widths, preview_limit)
        confidence = _confidence(fixed_score if not widths else 1.0)
        format_kind = "字段定长文件"
    else:
        headers = ["行号", "原始内容"]
        rows = [(str(index), line) for index, line in enumerate(lines[:preview_limit], start=1)]
        confidence = "低"
        format_kind = "原始文本/未知接口"

    if len(lines) > preview_limit:
        warnings.append(f"文件共有{len(lines):,}行，当前表格仅预览前{preview_limit:,}行。")

    interface_hint = _interface_hint(file_path.name, lines[:20], format_kind)
    details = [
        ("文件名", file_path.name),
        ("文件大小", f"{len(data):,}bytes"),
        ("编码", encoding),
        ("识别类型", interface_hint),
        ("解析模式", format_kind),
        ("识别置信度", confidence),
        ("分隔符", delimiter_name or "-"),
        ("记录长度", str(record_length) if record_length else "-"),
        ("总行数", f"{len(lines):,}"),
        ("展示行数", f"{len(rows):,}"),
        ("列数", f"{len(headers):,}"),
    ]
    return FinanceParseResult(
        path=str(file_path),
        encoding=encoding,
        format_kind=format_kind,
        confidence=confidence,
        delimiter=detected_delimiter or "",
        delimiter_name=delimiter_name,
        record_length=str(record_length) if record_length else "",
        total_rows=len(lines),
        displayed_rows=len(rows),
        headers=headers,
        rows=rows,
        raw_preview="\n".join(lines[:500]),
        details=details,
        warnings=warnings,
    )


def _parse_dbf(file_path: Path, data: bytes, preview_limit: int) -> FinanceParseResult:
    if len(data) < 32:
        raise ValueError("DBF文件头长度不足")
    record_count = int.from_bytes(data[4:8], "little", signed=False)
    header_length = int.from_bytes(data[8:10], "little", signed=False)
    record_length = int.from_bytes(data[10:12], "little", signed=False)
    fields: list[tuple[str, int]] = []
    pos = 32
    while pos + 32 <= len(data) and data[pos] != 0x0D:
        descriptor = data[pos: pos + 32]
        raw_name = descriptor[:11].split(b"\x00", 1)[0]
        name = _decode_cell(raw_name).strip() or f"F{len(fields) + 1:03d}"
        length = descriptor[16]
        if length <= 0:
            break
        fields.append((name, length))
        pos += 32
    if not fields:
        raise ValueError("未识别到DBF字段描述")

    rows: list[tuple[str, ...]] = []
    start = header_length
    max_records = min(record_count, preview_limit)
    for index in range(max_records):
        record_start = start + index * record_length
        record = data[record_start: record_start + record_length]
        if len(record) < record_length:
            break
        if record[:1] == b"*":
            continue
        cursor = 1
        cells: list[str] = []
        for _name, length in fields:
            cells.append(_decode_cell(record[cursor: cursor + length]).strip())
            cursor += length
        rows.append(tuple(cells))

    warnings: list[str] = []
    if record_count > preview_limit:
        warnings.append(f"DBF共有{record_count:,}条记录，当前表格仅预览前{preview_limit:,}条。")
    headers = [name for name, _length in fields]
    details = [
        ("文件名", file_path.name),
        ("文件大小", f"{len(data):,}bytes"),
        ("编码", "GB18030/ASCII"),
        ("识别类型", "DBF金融交换文件"),
        ("解析模式", "DBF字段表"),
        ("识别置信度", "高"),
        ("分隔符", "-"),
        ("记录长度", str(record_length)),
        ("总行数", f"{record_count:,}"),
        ("展示行数", f"{len(rows):,}"),
        ("列数", f"{len(headers):,}"),
    ]
    return FinanceParseResult(
        path=str(file_path),
        encoding="GB18030/ASCII",
        format_kind="DBF字段表",
        confidence="高",
        delimiter="",
        delimiter_name="",
        record_length=str(record_length),
        total_rows=record_count,
        displayed_rows=len(rows),
        headers=headers,
        rows=rows,
        raw_preview="\n".join("\t".join(row) for row in rows[:500]),
        details=details,
        warnings=warnings,
    )


def export_rows_csv(headers: list[str], rows: list[tuple[str, ...]]) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue()


def _decode_data(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "UTF-8-BOM"
    for encoding in ENCODING_CANDIDATES:
        try:
            return data.decode(encoding), encoding.upper()
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace"), "LATIN-1(替换)"


def _decode_cell(data: bytes) -> str:
    for encoding in ("gb18030", "utf-8", "big5", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _detect_delimiter(lines: list[str]) -> tuple[str, str, float]:
    best_delimiter = ""
    best_name = ""
    best_score = 0.0
    if not lines:
        return "", "", 0.0
    for delimiter, name in DELIMITER_CANDIDATES:
        counts: list[int] = []
        for line in lines:
            try:
                row = next(csv.reader([line], delimiter=delimiter))
            except csv.Error:
                continue
            if len(row) > 1:
                counts.append(len(row))
        if not counts:
            continue
        common = max(set(counts), key=counts.count)
        consistency = counts.count(common) / max(1, len(lines))
        richness = min(1.0, common / 4)
        coverage = len(counts) / max(1, len(lines))
        score = consistency * 0.65 + richness * 0.2 + coverage * 0.15
        if score > best_score:
            best_delimiter = delimiter
            best_name = name
            best_score = score
    return best_delimiter, best_name, best_score


def _detect_fixed_width(lines: list[str]) -> tuple[float, int]:
    usable = [line for line in lines if line]
    if len(usable) < 2:
        return 0.0, len(usable[0]) if usable else 0
    lengths = [len(line) for line in usable]
    common = max(set(lengths), key=lengths.count)
    consistency = lengths.count(common) / len(lengths)
    no_delimiter_bonus = 0.2 if not _detect_delimiter(usable)[0] else 0.0
    return min(1.0, consistency + no_delimiter_bonus), common


def _parse_delimited(lines: list[str], delimiter: str, has_header: bool, limit: int) -> tuple[list[str], list[tuple[str, ...]]]:
    parsed: list[list[str]] = []
    max_columns = 0
    for line in lines[:limit + (1 if has_header else 0)]:
        try:
            row = next(csv.reader([line], delimiter=delimiter))
        except csv.Error:
            row = [line]
        parsed.append([cell.strip() for cell in row])
        max_columns = max(max_columns, len(row))
    if has_header and parsed:
        headers = _normalize_headers(parsed[0], max_columns)
        body = parsed[1: limit + 1]
    else:
        headers = _default_headers(max_columns)
        body = parsed[:limit]
    return headers, [_pad_row(row, len(headers)) for row in body]


def _parse_fixed_width(lines: list[str], widths: list[int], limit: int) -> tuple[list[str], list[tuple[str, ...]]]:
    headers = [f"F{index:03d}({width})" for index, width in enumerate(widths, start=1)]
    rows: list[tuple[str, ...]] = []
    for line in lines[:limit]:
        pos = 0
        cells: list[str] = []
        for width in widths:
            cells.append(line[pos: pos + width].strip())
            pos += width
        if pos < len(line):
            cells.append(line[pos:].strip())
        rows.append(tuple(cells))
    if rows and len(rows[0]) > len(headers):
        headers.append("剩余内容")
    return headers, rows


def _infer_fixed_widths(lines: list[str]) -> list[int]:
    usable = [line for line in lines if line]
    if len(usable) < 2:
        return []
    min_len = min(len(line) for line in usable)
    if min_len < 8:
        return []
    boundary_positions: list[int] = []
    for pos in range(1, min_len):
        blank_ratio = sum(1 for line in usable if line[pos - 1:pos].isspace()) / len(usable)
        if blank_ratio >= 0.88:
            boundary_positions.append(pos)
    if not boundary_positions:
        return []
    collapsed: list[int] = []
    last = -2
    for pos in boundary_positions:
        if pos != last + 1:
            collapsed.append(pos)
        last = pos
    widths: list[int] = []
    cursor = 0
    for boundary in collapsed:
        if boundary - cursor >= 2:
            widths.append(boundary - cursor)
            cursor = boundary
    tail = min_len - cursor
    if tail >= 2:
        widths.append(tail)
    return widths if len(widths) > 1 else []


def _parse_widths(widths_text: str) -> list[int]:
    widths: list[int] = []
    for chunk in widths_text.replace("，", ",").replace(" ", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = int(chunk)
        except ValueError:
            continue
        if value > 0:
            widths.append(value)
    return widths


def _decode_delimiter(value: str) -> str:
    aliases = {
        "tab": "\t",
        "\\t": "\t",
        "soh": "\x01",
        "0x01": "\x01",
        "\\x01": "\x01",
    }
    text = value.strip()
    return aliases.get(text.lower(), text[:1])


def _delimiter_display(delimiter: str) -> str:
    for candidate, name in DELIMITER_CANDIDATES:
        if candidate == delimiter:
            return name
    return delimiter or "-"


def _confidence(score: float) -> str:
    if score >= 0.88:
        return "高"
    if score >= 0.68:
        return "中"
    return "低"


def _default_headers(count: int) -> list[str]:
    return [f"F{index:03d}" for index in range(1, max(1, count) + 1)]


def _normalize_headers(raw: list[str], count: int) -> list[str]:
    headers: list[str] = []
    for index in range(count):
        value = raw[index].strip() if index < len(raw) else ""
        headers.append(value or f"F{index + 1:03d}")
    return headers


def _pad_row(row: list[str], count: int) -> tuple[str, ...]:
    cells = row[:count]
    if len(cells) < count:
        cells.extend("" for _ in range(count - len(cells)))
    return tuple(cells)


def _interface_hint(filename: str, preview_lines: list[str], format_kind: str) -> str:
    upper_name = filename.upper()
    joined = "\n".join(preview_lines[:10]).upper()
    if upper_name.startswith("OFD") or "OFD" in upper_name[:16] or "OFD" in joined[:80]:
        return f"OFD类基金接口文件/{format_kind}"
    if any(token in upper_name for token in ("TA", "CIS", "CSDC", "FUND", "ETF")):
        return f"证券基金接口文件/{format_kind}"
    return format_kind
