from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable


class CronError(ValueError):
    pass


MONTH_ALIASES = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

DOW_ALIASES = {
    "SUN": 0,
    "MON": 1,
    "TUE": 2,
    "WED": 3,
    "THU": 4,
    "FRI": 5,
    "SAT": 6,
}

DOW_CN = {
    0: "周日",
    1: "周一",
    2: "周二",
    3: "周三",
    4: "周四",
    5: "周五",
    6: "周六",
}


@dataclass(frozen=True)
class FieldSpec:
    raw: str
    min_value: int
    max_value: int
    values: frozenset[int] | None
    question: bool = False

    @property
    def is_any(self) -> bool:
        return self.values is None or len(self.values) == (self.max_value - self.min_value + 1)

    @property
    def is_single(self) -> bool:
        return self.values is not None and len(self.values) == 1

    def contains(self, value: int) -> bool:
        if self.values is None:
            return True
        return value in self.values


@dataclass(frozen=True)
class CronSpec:
    seconds: FieldSpec
    minutes: FieldSpec
    hours: FieldSpec
    days: FieldSpec
    months: FieldSpec
    weekdays: FieldSpec
    years: FieldSpec | None


def _convert_token(token: str, aliases: dict[str, int] | None) -> int:
    token = token.strip().upper()
    if aliases and token in aliases:
        return aliases[token]
    try:
        return int(token)
    except ValueError as exc:
        raise CronError(f"无法识别字段值: {token}") from exc


def _normalize(value: int, min_value: int, max_value: int, is_weekday: bool) -> int:
    if is_weekday and value == 7:
        value = 0
    if value < min_value or value > max_value:
        raise CronError(f"字段值 {value} 超出范围 {min_value}-{max_value}")
    return value


def parse_field(
    raw: str,
    min_value: int,
    max_value: int,
    *,
    aliases: dict[str, int] | None = None,
    allow_question: bool = False,
    is_weekday: bool = False,
) -> FieldSpec:
    text = raw.strip().upper()
    if not text:
        raise CronError("Cron 字段不能为空")
    if text == "?":
        if not allow_question:
            raise CronError("? 只能用于日或周字段")
        return FieldSpec(raw=text, min_value=min_value, max_value=max_value, values=None, question=True)
    if text == "*":
        return FieldSpec(raw=text, min_value=min_value, max_value=max_value, values=None)

    values: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise CronError("Cron 字段存在空片段")
        step = 1
        base = part
        if "/" in part:
            base, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) <= 0:
                raise CronError(f"步长无效: {part}")
            step = int(step_text)

        if base in {"*", "?"}:
            start, end = min_value, max_value
        elif "-" in base:
            left, right = base.split("-", 1)
            start = _normalize(_convert_token(left, aliases), min_value, max_value, is_weekday)
            end = _normalize(_convert_token(right, aliases), min_value, max_value, is_weekday)
            if is_weekday and start == 0 and right.strip() == "7":
                end = 0
            if end < start:
                raise CronError(f"范围起点不能大于终点: {part}")
        else:
            start = _normalize(_convert_token(base, aliases), min_value, max_value, is_weekday)
            end = max_value if "/" in part else start

        for value in range(start, end + 1, step):
            normalized = 0 if is_weekday and value == 7 else value
            values.add(normalized)

    if not values:
        raise CronError("Cron 字段没有可用取值")
    return FieldSpec(raw=text, min_value=min_value, max_value=max_value, values=frozenset(values))


def parse_cron(expression: str) -> CronSpec:
    parts = expression.strip().split()
    if len(parts) == 5:
        parts = ["0", *parts]
    if len(parts) not in {6, 7}:
        raise CronError("Cron 表达式需要 5、6 或 7 个字段")
    seconds, minutes, hours, days, months, weekdays = parts[:6]
    years = parts[6] if len(parts) == 7 else None
    return CronSpec(
        seconds=parse_field(seconds, 0, 59),
        minutes=parse_field(minutes, 0, 59),
        hours=parse_field(hours, 0, 23),
        days=parse_field(days, 1, 31, allow_question=True),
        months=parse_field(months, 1, 12, aliases=MONTH_ALIASES),
        weekdays=parse_field(weekdays, 0, 7, aliases=DOW_ALIASES, allow_question=True, is_weekday=True),
        years=parse_field(years, 1970, 2099) if years else None,
    )


def _values(field: FieldSpec) -> list[int]:
    if field.values is None:
        return list(range(field.min_value, field.max_value + 1))
    return sorted(field.values)


def _weekday_sunday_zero(dt: datetime) -> int:
    return (dt.weekday() + 1) % 7


def _date_matches(spec: CronSpec, dt: datetime) -> bool:
    if spec.years and not spec.years.contains(dt.year):
        return False
    if not spec.months.contains(dt.month):
        return False

    day_restricted = not spec.days.is_any and not spec.days.question
    week_restricted = not spec.weekdays.is_any and not spec.weekdays.question
    day_ok = spec.days.contains(dt.day)
    week_ok = spec.weekdays.contains(_weekday_sunday_zero(dt))

    if day_restricted and week_restricted:
        return day_ok and week_ok
    if day_restricted:
        return day_ok
    if week_restricted:
        return week_ok
    return True


def next_times(expression: str, count: int = 10, start: datetime | None = None) -> list[datetime]:
    spec = parse_cron(expression)
    begin = (start or datetime.now()).replace(microsecond=0) + timedelta(seconds=1)
    results: list[datetime] = []
    max_days = 366 * 8
    hours = _values(spec.hours)
    minutes = _values(spec.minutes)
    seconds = _values(spec.seconds)

    for offset in range(max_days):
        day = begin.date() + timedelta(days=offset)
        probe = datetime(day.year, day.month, day.day)
        if not _date_matches(spec, probe):
            continue
        for hour in hours:
            for minute in minutes:
                for second in seconds:
                    candidate = datetime(day.year, day.month, day.day, hour, minute, second)
                    if candidate >= begin:
                        results.append(candidate)
                        if len(results) >= count:
                            return results
    return results


def _single_value(field: FieldSpec) -> int | None:
    if field.is_single and field.values:
        return next(iter(field.values))
    return None


def _step_text(field: FieldSpec, unit: str) -> str | None:
    raw = field.raw
    if raw.startswith("*/"):
        return f"每 {raw[2:]} {unit}"
    if raw == "*":
        return f"每{unit}"
    return None


def _describe_values(values: Iterable[int], unit: str, mapper: dict[int, str] | None = None) -> str:
    mapped = []
    for value in values:
        mapped.append(mapper[value] if mapper and value in mapper else f"{value}{unit}")
    return "、".join(mapped)


def _describe_field(field: FieldSpec, unit: str, mapper: dict[int, str] | None = None) -> str:
    if field.question:
        return "不指定"
    step = _step_text(field, unit)
    if step:
        return step
    if field.values is None:
        return f"每{unit}"
    if field.is_single:
        value = _single_value(field)
        if mapper and value in mapper:
            return mapper[value]
        return f"第 {value} {unit}"
    return _describe_values(sorted(field.values), unit, mapper)


def describe_cron(expression: str) -> str:
    spec = parse_cron(expression)
    sec = _single_value(spec.seconds)
    minute = _single_value(spec.minutes)
    hour = _single_value(spec.hours)
    day = _single_value(spec.days)
    month = _single_value(spec.months)
    weekdays = sorted(spec.weekdays.values) if spec.weekdays.values is not None else []

    time_text = None
    if sec is not None and minute is not None and hour is not None:
        time_text = f"{hour:02d}:{minute:02d}:{sec:02d}"

    if time_text and spec.months.is_any and spec.days.is_any and spec.weekdays.is_any:
        return f"每天 {time_text} 执行"
    if time_text and spec.months.is_any and day is not None and spec.weekdays.question:
        return f"每月 {day} 日 {time_text} 执行"
    if time_text and spec.months.is_any and spec.days.question and weekdays:
        return f"每周 { _describe_values(weekdays, '', DOW_CN) } {time_text} 执行"
    if sec == 0 and spec.hours.is_any and spec.minutes.raw.startswith("*/"):
        return f"每 {spec.minutes.raw[2:]} 分钟执行"
    if sec == 0 and minute is not None and spec.hours.is_any:
        return f"每小时第 {minute} 分钟执行"
    if time_text and month is not None and day is not None:
        return f"每年 {month} 月 {day} 日 {time_text} 执行"

    parts = [
        f"秒: {_describe_field(spec.seconds, '秒')}",
        f"分: {_describe_field(spec.minutes, '分钟')}",
        f"时: {_describe_field(spec.hours, '点')}",
        f"日: {_describe_field(spec.days, '日')}",
        f"月: {_describe_field(spec.months, '月')}",
        f"周: {_describe_field(spec.weekdays, '', DOW_CN)}",
    ]
    if spec.years:
        parts.append(f"年: {_describe_field(spec.years, '年')}")
    return "；".join(parts)


PRESETS: dict[str, str] = {
    "每分钟": "0 * * * * ?",
    "每5分钟": "0 */5 * * * ?",
    "每小时": "0 0 * * * ?",
    "每天凌晨2点": "0 0 2 * * ?",
    "每周一9点": "0 0 9 ? * MON",
    "每月1号": "0 0 0 1 * ?",
    "工作日9点": "0 0 9 ? * MON-FRI",
}
