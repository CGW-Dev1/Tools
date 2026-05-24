from __future__ import annotations

import re
from dataclasses import dataclass


REGEX_TEMPLATES: dict[str, str] = {
    "中国手机号": r"^1[3-9]\d{9}$",
    "固定电话": r"^(?:0\d{2,3}-?)?\d{7,8}$",
    "邮政编码": r"^\d{6}$",
    "QQ号": r"^[1-9]\d{4,10}$",
    "微信号": r"^[A-Za-z][A-Za-z0-9_-]{5,19}$",
    "中文姓名": r"^[\u4e00-\u9fa5]{2,20}$",
    "用户名": r"^[A-Za-z][A-Za-z0-9_]{4,15}$",
    "邮箱": r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$",
    "URL": r"https?://[^\s/$.?#].[^\s]*",
    "域名": r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$",
    "IPv4": r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
    "MAC地址": r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$",
    "身份证号": r"^\d{17}[\dXx]$",
    "整数/小数": r"^-?\d+(?:\.\d+)?$",
    "金额": r"^(?:0|[1-9]\d*)(?:\.\d{1,2})?$",
    "日期 yyyy-MM-dd": r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$",
    "时间 HH:mm:ss": r"^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$",
    "日期时间": r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\s(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$",
    "UUID": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$",
    "十六进制颜色": r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
    "车牌号": r"^[\u4e00-\u9fa5][A-Z][A-Z0-9]{5,6}$",
    "Base64": r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$",
    "HTML标签": r"<([A-Za-z][A-Za-z0-9]*)\b[^>]*>.*?</\1>",
    "纯数字": r"^\d+$",
    "纯字母": r"^[A-Za-z]+$",
    "字母数字": r"^[A-Za-z0-9]+$",
    "中文字符": r"[\u4e00-\u9fa5]+",
    "空白行": r"^\s*$",
    "强密码": r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$",
}


KNOWN_EXPLANATIONS = {
    r"^1[3-9]\d{9}$": [
        "完整匹配 11 位中国大陆手机号。",
        "第 1 位固定为 1。",
        "第 2 位是 3 到 9 之间的数字。",
        "后面 9 位都是数字。",
    ],
    r"^(?:0\d{2,3}-?)?\d{7,8}$": [
        "完整匹配固定电话号码。",
        "区号可选，可以是 0 开头的 3 到 4 位数字。",
        "区号后面的短横线可选。",
        "号码主体是 7 到 8 位数字。",
    ],
    r"^\d{6}$": [
        "完整匹配 6 位数字。",
    ],
    r"^[1-9]\d{4,10}$": [
        "完整匹配 QQ 号。",
        "第 1 位是 1 到 9，不能是 0。",
        "后面跟 4 到 10 位数字。",
        "总长度 5 到 11 位。",
    ],
    r"^[A-Za-z][A-Za-z0-9_-]{5,19}$": [
        "完整匹配微信号。",
        "第 1 位必须是英文字母。",
        "后面 5 到 19 位可以是字母、数字、下划线或减号。",
        "总长度 6 到 20 位。",
    ],
    r"^[\u4e00-\u9fa5]{2,20}$": [
        "完整匹配中文姓名。",
        "由 2 到 20 个中文字符组成。",
    ],
    r"^[A-Za-z][A-Za-z0-9_]{4,15}$": [
        "完整匹配用户名。",
        "第 1 位必须是英文字母。",
        "后面 4 到 15 位可以是英文字母、数字或下划线。",
        "总长度 5 到 16 位。",
    ],
    r"^\d{17}[\dXx]$": [
        "完整匹配 18 位身份证号。",
        "前 17 位都是数字。",
        "最后 1 位可以是数字，也可以是 X 或 x。",
    ],
    r"[\u4e00-\u9fa5]+": [
        "匹配 1 个或多个中文字符。",
    ],
    r"^-?\d+(?:\.\d+)?$": [
        "完整匹配整数或小数。",
        "开头可以有负号。",
        "整数部分至少 1 位数字。",
        "小数部分可选，如果有小数点，后面至少 1 位数字。",
    ],
    r"^(?:0|[1-9]\d*)(?:\.\d{1,2})?$": [
        "完整匹配金额。",
        "整数部分可以是 0，或非 0 开头的数字。",
        "小数部分可选，最多 2 位数字。",
    ],
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$": [
        "完整匹配邮箱格式。",
        "@ 前面至少 1 位，可包含字母、数字、点、下划线、百分号、加号、减号。",
        "@ 后面是域名，可包含字母、数字、点、减号。",
        "最后的后缀至少 2 位英文字母。",
    ],
    r"https?://[^\s/$.?#].[^\s]*": [
        "匹配 http 或 https 开头的网址。",
        "协议后面必须有 ://。",
        "后面继续匹配非空白字符组成的网址内容。",
    ],
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$": [
        "完整匹配域名。",
        "由一段或多段域名标签组成，中间用点号分隔。",
        "每段可包含字母、数字、减号，但不能以减号开头或结尾。",
        "最后的顶级域名至少 2 位英文字母。",
    ],
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b": [
        "匹配 IPv4 地址。",
        "由 4 段数字组成，每段范围是 0 到 255。",
        "每段之间用点号分隔。",
    ],
    r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$": [
        "完整匹配 MAC 地址。",
        "由 6 组十六进制字符组成。",
        "每组 2 位，组与组之间用冒号分隔。",
    ],
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$": [
        "完整匹配日期，格式为 yyyy-MM-dd。",
        "年份是 4 位数字。",
        "月份范围是 01 到 12。",
        "日期范围是 01 到 31。",
    ],
    r"^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$": [
        "完整匹配时间，格式为 HH:mm:ss。",
        "小时范围是 00 到 23。",
        "分钟和秒范围是 00 到 59。",
    ],
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\s(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$": [
        "完整匹配日期时间，格式为 yyyy-MM-dd HH:mm:ss。",
        "日期部分是 4 位年份、2 位月份、2 位日期。",
        "时间部分是 2 位小时、2 位分钟、2 位秒。",
    ],
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$": [
        "完整匹配 UUID。",
        "由 32 位十六进制字符和 4 个短横线组成。",
        "分组长度为 8-4-4-4-12。",
    ],
    r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$": [
        "完整匹配十六进制颜色值。",
        "以 # 开头。",
        "后面可以是 3 位或 6 位十六进制字符。",
    ],
    r"^[\u4e00-\u9fa5][A-Z][A-Z0-9]{5,6}$": [
        "完整匹配简化车牌号。",
        "第 1 位是中文省份简称。",
        "第 2 位是大写英文字母。",
        "后面 5 到 6 位是大写字母或数字。",
    ],
    r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$": [
        "完整匹配 Base64 字符串。",
        "主体由字母、数字、加号和斜杠组成。",
        "长度按 4 位一组排列，末尾可以有 = 补位。",
    ],
    r"<([A-Za-z][A-Za-z0-9]*)\b[^>]*>.*?</\1>": [
        "匹配成对 HTML 标签。",
        "开始标签名以英文字母开头，可跟字母或数字。",
        "允许标签属性。",
        "结束标签需要与开始标签同名。",
    ],
    r"^\d+$": [
        "完整匹配纯数字。",
        "至少 1 位数字。",
    ],
    r"^[A-Za-z]+$": [
        "完整匹配纯英文字母。",
        "至少 1 位字母。",
    ],
    r"^[A-Za-z0-9]+$": [
        "完整匹配字母或数字。",
        "至少 1 位，可由英文字母和数字组成。",
    ],
    r"^\s*$": [
        "完整匹配空白行。",
        "可以是空字符串，也可以只包含空格、制表符等空白字符。",
    ],
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$": [
        "完整匹配强密码。",
        "长度至少 8 位。",
        "必须包含小写字母、大写字母、数字和特殊字符。",
    ],
}


@dataclass
class RegexPart:
    text: str
    desc: str
    min_count: int
    max_count: int | None
    fixed: bool


def explain_regex(pattern: str) -> str:
    if not pattern:
        return "输入正则后会在这里显示简要释义。"

    if pattern in KNOWN_EXPLANATIONS:
        return _format_explanation(KNOWN_EXPLANATIONS[pattern])

    exact = pattern.startswith("^") and pattern.endswith("$") and len(pattern) >= 2
    body = pattern[1:-1] if exact else pattern
    parts = _parse_simple_sequence(body)
    if not parts:
        return _fallback_explanation(pattern, exact)

    lines: list[str] = ["完整匹配整段文本。" if exact else "匹配文本中符合规则的片段。"]
    total = _fixed_total(parts)
    position = 1
    for part in parts:
        if total is not None and part.fixed:
            lines.append(_position_line(position, part))
            position += part.min_count
        else:
            lines.append(_count_line(part))
    if total is not None:
        lines.append(f"总长度：{total} 位。")
    return _format_explanation(lines)


def _format_explanation(lines: list[str]) -> str:
    return "\n".join(f"{index}. {line}" for index, line in enumerate(lines, start=1))


def _parse_simple_sequence(pattern: str) -> list[RegexPart]:
    parts: list[RegexPart] = []
    index = 0
    while index < len(pattern):
        atom, desc, next_index = _read_atom(pattern, index)
        if not atom:
            return []
        min_count, max_count, after_quantifier = _read_quantifier(pattern, next_index)
        parts.append(
            RegexPart(
                text=atom,
                desc=desc,
                min_count=min_count,
                max_count=max_count,
                fixed=max_count is not None and min_count == max_count,
            )
        )
        index = after_quantifier
    return parts


def _read_atom(pattern: str, index: int) -> tuple[str, str, int]:
    ch = pattern[index]
    if ch == "\\":
        if index + 1 >= len(pattern):
            return "", "", index
        token = pattern[index : index + 2]
        if token == r"\d":
            return token, "数字", index + 2
        if token == r"\w":
            return token, "字母、数字或下划线", index + 2
        if token == r"\s":
            return token, "空白字符", index + 2
        if token == r"\.":
            return token, "点号", index + 2
        if token.startswith(r"\u") and index + 5 < len(pattern):
            token = pattern[index : index + 6]
            return token, f"Unicode 字符 {token}", index + 6
        return token, token[1], index + 2

    if ch == "[":
        end = _find_closing(pattern, index, "[", "]")
        if end == -1:
            return "", "", index
        token = pattern[index : end + 1]
        return token, _describe_class(token), end + 1

    if ch == ".":
        return ch, "任意字符", index + 1

    if ch in "()|":
        return "", "", index

    return ch, f"字符 {ch}", index + 1


def _read_quantifier(pattern: str, index: int) -> tuple[int, int | None, int]:
    if index >= len(pattern):
        return 1, 1, index
    ch = pattern[index]
    if ch == "?":
        return 0, 1, index + 1
    if ch == "+":
        return 1, None, index + 1
    if ch == "*":
        return 0, None, index + 1
    if ch != "{":
        return 1, 1, index
    end = pattern.find("}", index + 1)
    if end == -1:
        return 1, 1, index
    raw = pattern[index + 1 : end]
    if "," in raw:
        left, right = raw.split(",", 1)
        min_count = int(left) if left.strip().isdigit() else 0
        max_count = int(right) if right.strip().isdigit() else None
        return min_count, max_count, end + 1
    if raw.strip().isdigit():
        count = int(raw)
        return count, count, end + 1
    return 1, 1, index


def _describe_class(token: str) -> str:
    body = token[1:-1]
    negated = body.startswith("^")
    if negated:
        body = body[1:]

    if body in {r"\dXx", r"0-9Xx"}:
        desc = "数字或 X/x"
    elif body == r"\u4e00-\u9fa5":
        desc = "中文字符"
    elif body in {"0-9", r"\d"}:
        desc = "数字"
    elif body == "3-9":
        desc = "3 到 9 之间的数字"
    elif body == "A-Za-z":
        desc = "英文字母"
    elif body == "A-Z":
        desc = "大写字母"
    elif body == "a-z":
        desc = "小写字母"
    elif body == "A-Za-z0-9":
        desc = "英文字母或数字"
    elif body == "A-Za-z0-9_":
        desc = "英文字母、数字或下划线"
    elif body == "A-Za-z0-9._%+-":
        desc = "英文字母、数字、点、下划线、百分号、加号或减号"
    elif body == "A-Za-z0-9.-":
        desc = "英文字母、数字、点或减号"
    else:
        desc = "指定字符集合中的字符"

    return f"不是{desc}" if negated else desc


def _count_line(part: RegexPart) -> str:
    desc = part.desc
    if part.min_count == 0 and part.max_count == 1:
        return f"可选 1 位{desc}。"
    if part.max_count is None:
        if part.min_count == 0:
            return f"后面可以跟 0 位或多位{desc}。"
        return f"至少 {part.min_count} 位{desc}。"
    if part.min_count == part.max_count:
        return f"{part.min_count} 位{desc}。"
    return f"{part.min_count} 到 {part.max_count} 位{desc}。"


def _position_line(position: int, part: RegexPart) -> str:
    if part.min_count == 1:
        return f"第 {position} 位是{_clean_desc(part.desc)}。"
    end = position + part.min_count - 1
    return f"第 {position}-{end} 位是{_clean_desc(part.desc)}。"


def _clean_desc(desc: str) -> str:
    if desc.startswith("字符 "):
        return desc.replace("字符 ", "", 1)
    return desc


def _fixed_total(parts: list[RegexPart]) -> int | None:
    total = 0
    for part in parts:
        if not part.fixed:
            return None
        total += part.min_count
    return total


def _fallback_explanation(pattern: str, exact: bool) -> str:
    lines = ["完整匹配整段文本。" if exact else "匹配文本中符合规则的片段。"]
    if r"\d" in pattern:
        lines.append("包含数字匹配。")
    if re.search(r"\[[^\]]*A-Za-z[^\]]*\]", pattern):
        lines.append("包含英文字母匹配。")
    if "+" in pattern:
        lines.append("有 1 个或多个重复的部分。")
    if "*" in pattern:
        lines.append("有 0 个或多个重复的部分。")
    if "?" in pattern:
        lines.append("有可选的部分。")
    if len(lines) == 1:
        lines.append("当前表达式包含较复杂结构，建议查看匹配结果验证。")
    return _format_explanation(lines)


def _find_closing(pattern: str, start: int, open_char: str, close_char: str) -> int:
    escaped = False
    depth = 0
    for index in range(start, len(pattern)):
        ch = pattern[index]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return index
    return -1
