from __future__ import annotations

import difflib
import html
import zipfile
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from xml.etree import ElementTree


@dataclass
class DiffSegment:
    text: str
    tag: str


@dataclass
class DiffRow:
    kind: str
    left_range: str
    right_range: str
    left_text: str
    right_text: str


@dataclass
class DiffResult:
    segments: list[DiffSegment]
    rows: list[DiffRow]
    text_export: str
    html_export: str
    added: int
    removed: int
    changed: int

    @property
    def equal(self) -> bool:
        return self.added == 0 and self.removed == 0 and self.changed == 0

    @property
    def summary(self) -> str:
        if self.equal:
            return "两个文档内容一致"
        return f"新增{self.added}行，删除{self.removed}行，修改{self.changed}处"


class DocumentReadError(ValueError):
    pass


def read_document_file(path: str | Path) -> str:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".docx":
        return _read_docx(file_path)
    if suffix == ".doc":
        raise DocumentReadError("暂不支持.doc二进制格式，请转为.docx或文本文件")
    if suffix == ".pdf":
        raise DocumentReadError("暂不支持PDF解析，请导入文本、代码、Markdown或.docx文件")
    data = file_path.read_bytes()
    return _decode_text(data)


def build_document_diff(
    left_text: str,
    right_text: str,
    *,
    ignore_case: bool = False,
    collapse_whitespace: bool = False,
) -> DiffResult:
    left_lines = left_text.splitlines()
    right_lines = right_text.splitlines()
    left_keys = [_normalize_line(line, ignore_case, collapse_whitespace) for line in left_lines]
    right_keys = [_normalize_line(line, ignore_case, collapse_whitespace) for line in right_lines]
    matcher = difflib.SequenceMatcher(None, left_keys, right_keys, autojunk=False)

    rows: list[DiffRow] = []
    segments: list[DiffSegment] = []
    added = 0
    removed = 0
    changed = 0

    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        left_block = left_lines[left_start:left_end]
        right_block = right_lines[right_start:right_end]
        if tag == "equal":
            _append_equal_preview(segments, left_start, right_start, left_block)
            continue

        segments.append(
            DiffSegment(
                f"@@文档1{_line_range(left_start, left_end)}->文档2{_line_range(right_start, right_end)}@@\n",
                "diff_header",
            )
        )
        if tag == "replace":
            changed += max(1, min(len(left_block), len(right_block)))
            added += max(0, len(right_block) - len(left_block))
            removed += max(0, len(left_block) - len(right_block))
            rows.extend(_paired_diff_rows(left_lines, right_lines, left_start, left_end, right_start, right_end, ignore_case, collapse_whitespace))
            for line in left_block:
                segments.append(DiffSegment(f"-{line}\n", "diff_delete"))
            for line in right_block:
                segments.append(DiffSegment(f"+{line}\n", "diff_insert"))
        elif tag == "delete":
            removed += left_end - left_start
            rows.extend(DiffRow("删除", str(index + 1), "-", _display_line(left_lines[index]), "") for index in range(left_start, left_end))
            for line in left_block:
                segments.append(DiffSegment(f"-{line}\n", "diff_delete"))
        elif tag == "insert":
            added += right_end - right_start
            rows.extend(DiffRow("新增", "-", str(index + 1), "", _display_line(right_lines[index])) for index in range(right_start, right_end))
            for line in right_block:
                segments.append(DiffSegment(f"+{line}\n", "diff_insert"))

    if not segments:
        segments.append(DiffSegment("两个文档内容一致\n", "success"))
    text_export = _build_text_export(left_lines, right_lines, left_keys, right_keys)
    html_export = _build_html_export(left_lines, right_lines, left_keys, right_keys)
    return DiffResult(
        segments=segments,
        rows=rows,
        text_export=text_export,
        html_export=html_export,
        added=added,
        removed=removed,
        changed=changed,
    )


def _decode_text(data: bytes) -> str:
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16-le")
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16-be")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    for encoding in ("utf-8", "gb18030", "utf-16", "big5", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentReadError("无法识别文件编码")


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise DocumentReadError("未找到Word正文内容") from exc
    except zipfile.BadZipFile as exc:
        raise DocumentReadError("不是有效的.docx文件") from exc

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocumentReadError("Word正文XML解析失败") from exc
    paragraphs: list[str] = []
    for paragraph in root.iter(_word_tag("p")):
        parts: list[str] = []
        for node in paragraph.iter():
            tag = _strip_namespace(node.tag)
            if tag == "t":
                parts.append(node.text or "")
            elif tag == "tab":
                parts.append("\t")
            elif tag in {"br", "cr"}:
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _word_tag(name: str) -> str:
    return f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{name}"


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _normalize_line(line: str, ignore_case: bool, collapse_whitespace: bool) -> str:
    value = " ".join(line.split()) if collapse_whitespace else line
    return value.casefold() if ignore_case else value


def _append_equal_preview(
    segments: list[DiffSegment],
    left_start: int,
    right_start: int,
    lines: list[str],
) -> None:
    if not lines:
        return
    context = 3
    if len(lines) <= context * 2 + 2:
        for offset, line in enumerate(lines):
            segments.append(DiffSegment(f" {left_start + offset + 1:>4}|{right_start + offset + 1:>4}|{line}\n", "diff_equal"))
        return
    for offset, line in enumerate(lines[:context]):
        segments.append(DiffSegment(f" {left_start + offset + 1:>4}|{right_start + offset + 1:>4}|{line}\n", "diff_equal"))
    hidden = len(lines) - context * 2
    segments.append(DiffSegment(f"...省略{hidden}行相同内容...\n", "muted"))
    tail_start = len(lines) - context
    for offset, line in enumerate(lines[tail_start:], start=tail_start):
        segments.append(DiffSegment(f" {left_start + offset + 1:>4}|{right_start + offset + 1:>4}|{line}\n", "diff_equal"))


def _line_range(start: int, end: int) -> str:
    if end <= start:
        return "-"
    first = start + 1
    last = end
    return str(first) if first == last else f"{first}-{last}"


def _paired_diff_rows(
    left_lines: list[str],
    right_lines: list[str],
    left_start: int,
    left_end: int,
    right_start: int,
    right_end: int,
    ignore_case: bool,
    collapse_whitespace: bool,
) -> list[DiffRow]:
    rows: list[DiffRow] = []
    total = max(left_end - left_start, right_end - right_start)
    for offset in range(total):
        left_index = left_start + offset if left_start + offset < left_end else None
        right_index = right_start + offset if right_start + offset < right_end else None
        left_line = left_lines[left_index] if left_index is not None else None
        right_line = right_lines[right_index] if right_index is not None else None
        if left_line is None and right_line is None:
            continue
        if left_line is None:
            rows.append(DiffRow("新增", "-", str(right_index + 1), "", _display_line(right_line)))
            continue
        if right_line is None:
            rows.append(DiffRow("删除", str(left_index + 1), "-", _display_line(left_line), ""))
            continue
        if _normalize_line(left_line, ignore_case, collapse_whitespace) == _normalize_line(right_line, ignore_case, collapse_whitespace):
            continue
        rows.append(DiffRow("修改", str(left_index + 1), str(right_index + 1), _display_line(left_line), _display_line(right_line)))
    return rows


def _display_line(line: str | None) -> str:
    if line is None:
        return ""
    if line == "":
        return "空行"
    if line.strip() == "":
        return _blank_label(line)
    return line


def _blank_label(line: str) -> str:
    spaces = line.count(" ")
    tabs = line.count("\t")
    parts: list[str] = []
    if spaces:
        parts.append(f"{spaces}个空格")
    if tabs:
        parts.append(f"{tabs}个Tab")
    return "空白行(" + "，".join(parts or [f"{len(line)}个空白字符"]) + ")"


def _build_text_export(left_lines: list[str], right_lines: list[str], left_keys: list[str], right_keys: list[str]) -> str:
    matcher = difflib.SequenceMatcher(None, left_keys, right_keys, autojunk=False)
    lines = [
        "文档对比结果",
        "说明：文档1中的差异用[-内容-]标注，文档2中的差异用{+内容+}标注。",
        "",
        "文档1行\t文档1内容\t文档2行\t文档2内容",
    ]
    changed = False
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        left_block = left_lines[left_start:left_end]
        right_block = right_lines[right_start:right_end]
        if tag == "equal":
            for offset, line in enumerate(left_block):
                lines.append(f"{left_start + offset + 1}\t{line}\t{right_start + offset + 1}\t{line}")
            continue
        changed = True
        if tag == "replace":
            for offset, pair in enumerate(zip_longest(left_block, right_block, fillvalue=None)):
                left_line, right_line = pair
                left_no = str(left_start + offset + 1) if offset < len(left_block) else ""
                right_no = str(right_start + offset + 1) if offset < len(right_block) else ""
                if left_line is None:
                    lines.append(f"\t\t{right_no}\t{{+{_display_line(right_line)}+}}")
                    continue
                if right_line is None:
                    lines.append(f"{left_no}\t[-{_display_line(left_line)}-]\t\t")
                    continue
                left_rendered, right_rendered = _inline_diff_text(left_line, right_line)
                left_rendered = _visible_text_line(left_line, left_rendered)
                right_rendered = _visible_text_line(right_line, right_rendered)
                lines.append(f"{left_no}\t{left_rendered}\t{right_no}\t{right_rendered}")
        elif tag == "delete":
            for offset, line in enumerate(left_block):
                lines.append(f"{left_start + offset + 1}\t[-{_display_line(line)}-]\t\t")
        elif tag == "insert":
            for offset, line in enumerate(right_block):
                lines.append(f"\t\t{right_start + offset + 1}\t{{+{_display_line(line)}+}}")
    return "\n".join(lines) if changed else "两个文档内容一致"


def _build_html_export(
    left_lines: list[str],
    right_lines: list[str],
    left_keys: list[str],
    right_keys: list[str],
) -> str:
    matcher = difflib.SequenceMatcher(None, left_keys, right_keys, autojunk=False)
    body_rows: list[str] = []
    changed = False
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        left_block = left_lines[left_start:left_end]
        right_block = right_lines[right_start:right_end]
        if tag == "equal":
            for offset, line in enumerate(left_block):
                body_rows.append(_html_row("equal", left_start + offset + 1, html.escape(line), right_start + offset + 1, html.escape(line)))
        elif tag == "replace":
            changed = True
            for offset, pair in enumerate(zip_longest(left_block, right_block, fillvalue=None)):
                left_line, right_line = pair
                left_no = left_start + offset + 1 if offset < len(left_block) else ""
                right_no = right_start + offset + 1 if offset < len(right_block) else ""
                if left_line is not None and right_line is not None:
                    left_html, right_html = _inline_diff_html(left_line, right_line)
                    left_html = _visible_html_line(left_line, left_html)
                    right_html = _visible_html_line(right_line, right_html)
                    body_rows.append(_html_row("changed", left_no, left_html, right_no, right_html))
                elif left_line is not None:
                    body_rows.append(_html_row("deleted", left_no, _token("del-token", _display_line(left_line)), "", ""))
                else:
                    body_rows.append(_html_row("inserted", "", "", right_no, _token("ins-token", _display_line(right_line))))
        elif tag == "delete":
            changed = True
            for offset, line in enumerate(left_block):
                body_rows.append(_html_row("deleted", left_start + offset + 1, _token("del-token", _display_line(line)), "", ""))
        elif tag == "insert":
            changed = True
            for offset, line in enumerate(right_block):
                body_rows.append(_html_row("inserted", "", "", right_start + offset + 1, _token("ins-token", _display_line(line))))

    if not changed:
        body_rows = ['<tr><td colspan="4" class="empty">两个文档内容一致</td></tr>']

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>文档对比结果</title>
<style>
body{{margin:0;background:#f4f6fa;color:#1f2937;font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;}}
.wrap{{padding:24px;}}
h1{{font-size:22px;margin:0 0 8px;}}
.legend{{display:flex;gap:16px;align-items:center;margin:0 0 18px;color:#526071;font-size:13px;}}
.badge{{display:inline-flex;align-items:center;gap:6px;}}
.swatch{{width:18px;height:12px;border-radius:3px;border:1px solid #d7dee9;}}
.old{{background:#fecaca;}}
.new{{background:#bbf7d0;}}
table{{width:100%;border-collapse:collapse;table-layout:fixed;background:#fff;border:1px solid #d7dee9;}}
th,td{{border:1px solid #d7dee9;padding:8px 10px;vertical-align:top;font-size:14px;line-height:1.65;word-break:break-word;white-space:pre-wrap;}}
th{{background:#eef2f7;font-weight:600;text-align:left;}}
.no{{width:72px;text-align:right;color:#657386;background:#f7f9fc;}}
.equal td{{background:#fff;}}
.inserted td{{background:#dcfce7;}}
.deleted td{{background:#fee2e2;}}
.changed td{{background:#fef3c7;}}
.del-token{{background:#fecaca;color:#991b1b;border-radius:3px;padding:1px 2px;text-decoration:line-through;text-decoration-thickness:1px;}}
.ins-token{{background:#bbf7d0;color:#166534;border-radius:3px;padding:1px 2px;font-weight:600;}}
.blank-token{{background:#e5e7eb;color:#526071;border:1px dashed #94a3b8;border-radius:3px;padding:1px 5px;}}
.empty{{text-align:center;color:#657386;padding:32px;}}
</style>
</head>
<body>
<div class="wrap">
<h1>文档对比结果</h1>
<div class="legend">
<span class="badge"><span class="swatch old"></span>文档1删除或变更内容</span>
<span class="badge"><span class="swatch new"></span>文档2新增或变更内容</span>
</div>
<table>
<thead><tr><th class="no">文档1行号</th><th>文档1内容</th><th class="no">文档2行号</th><th>文档2内容</th></tr></thead>
<tbody>
{''.join(body_rows)}
</tbody>
</table>
</div>
</body>
</html>
"""


def _html_row(css_class: str, left_no: int | str, left_html: str, right_no: int | str, right_html: str) -> str:
    return (
        f'<tr class="{css_class}">'
        f'<td class="no">{html.escape(str(left_no))}</td>'
        f"<td>{left_html}</td>"
        f'<td class="no">{html.escape(str(right_no))}</td>'
        f"<td>{right_html}</td>"
        "</tr>"
    )


def _visible_html_line(line: str, rendered: str) -> str:
    if line == "" or line.strip() == "":
        return _token("blank-token", _display_line(line))
    return rendered


def _visible_text_line(line: str, rendered: str) -> str:
    if line == "" or line.strip() == "":
        return _display_line(line)
    return rendered


def _inline_diff_html(left_text: str, right_text: str) -> tuple[str, str]:
    matcher = difflib.SequenceMatcher(None, left_text, right_text, autojunk=False)
    left_parts: list[str] = []
    right_parts: list[str] = []
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        left_piece = left_text[left_start:left_end]
        right_piece = right_text[right_start:right_end]
        if tag == "equal":
            left_parts.append(html.escape(left_piece))
            right_parts.append(html.escape(right_piece))
        elif tag == "delete":
            left_parts.append(_token("del-token", left_piece))
        elif tag == "insert":
            right_parts.append(_token("ins-token", right_piece))
        elif tag == "replace":
            left_parts.append(_token("del-token", left_piece))
            right_parts.append(_token("ins-token", right_piece))
    return "".join(left_parts), "".join(right_parts)


def _inline_diff_text(left_text: str, right_text: str) -> tuple[str, str]:
    matcher = difflib.SequenceMatcher(None, left_text, right_text, autojunk=False)
    left_parts: list[str] = []
    right_parts: list[str] = []
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        left_piece = left_text[left_start:left_end]
        right_piece = right_text[right_start:right_end]
        if tag == "equal":
            left_parts.append(left_piece)
            right_parts.append(right_piece)
        elif tag == "delete":
            left_parts.append(f"[-{left_piece}-]")
        elif tag == "insert":
            right_parts.append(f"{{+{right_piece}+}}")
        elif tag == "replace":
            left_parts.append(f"[-{left_piece}-]")
            right_parts.append(f"{{+{right_piece}+}}")
    return "".join(left_parts), "".join(right_parts)


def _token(css_class: str, text: str) -> str:
    return f'<span class="{css_class}">{html.escape(text)}</span>'
