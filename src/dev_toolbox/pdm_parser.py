from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PdmColumn:
    id: str
    name: str
    code: str
    data_type: str
    length: str
    mandatory: str
    comment: str
    primary_key: bool = False


@dataclass
class PdmIndex:
    name: str
    code: str
    unique: bool
    columns: list[str] = field(default_factory=list)


@dataclass
class PdmTable:
    id: str
    name: str
    code: str
    comment: str
    columns: list[PdmColumn] = field(default_factory=list)
    indexes: list[PdmIndex] = field(default_factory=list)


@dataclass
class PdmModel:
    name: str
    tables: list[PdmTable]


def parse_pdm(path: str | Path) -> PdmModel:
    tree = ET.parse(path)
    root = tree.getroot()
    model_name = _child_text(root, "Name") or Path(path).stem
    tables: list[PdmTable] = []

    for table_el in _iter_local(root, "Table"):
        table = PdmTable(
            id=table_el.attrib.get("Id", ""),
            name=_child_text(table_el, "Name"),
            code=_child_text(table_el, "Code"),
            comment=_child_text(table_el, "Comment"),
        )
        column_by_id: dict[str, PdmColumn] = {}
        columns_container = _child(table_el, "Columns")
        if columns_container is not None:
            for column_el in _children_local(columns_container, "Column"):
                column = PdmColumn(
                    id=column_el.attrib.get("Id", ""),
                    name=_child_text(column_el, "Name"),
                    code=_child_text(column_el, "Code"),
                    data_type=_child_text(column_el, "DataType"),
                    length=_child_text(column_el, "Length"),
                    mandatory=_child_text(column_el, "Mandatory"),
                    comment=_child_text(column_el, "Comment"),
                )
                table.columns.append(column)
                if column.id:
                    column_by_id[column.id] = column

        primary_refs = _primary_key_column_refs(table_el)
        for ref in primary_refs:
            if ref in column_by_id:
                column_by_id[ref].primary_key = True

        table.indexes = _parse_indexes(table_el, column_by_id)
        if table.code or table.name or table.columns:
            tables.append(table)

    tables.sort(key=lambda item: (item.code or item.name).lower())
    return PdmModel(name=model_name, tables=tables)


def export_table_markdown(table: PdmTable) -> str:
    title = table.code or table.name or "未命名表"
    lines = [f"# {title}", ""]
    if table.name and table.name != title:
        lines.append(f"- 名称: {table.name}")
    if table.comment:
        lines.append(f"- 备注: {table.comment}")
    if len(lines) > 2:
        lines.append("")
    lines.extend(
        [
            "| 序号 | 字段名 | 名称 | 类型 | 长度 | 主键 | 必填 | 备注 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for index, column in enumerate(table.columns, start=1):
        lines.append(
            "| {index} | {code} | {name} | {typ} | {length} | {pk} | {mandatory} | {comment} |".format(
                index=index,
                code=_md(column.code),
                name=_md(column.name),
                typ=_md(column.data_type),
                length=_md(column.length),
                pk="是" if column.primary_key else "",
                mandatory="是" if column.mandatory in {"1", "true", "TRUE", "True"} else "",
                comment=_md(column.comment),
            )
        )
    if table.indexes:
        lines.extend(["", "## 索引", "", "| 名称 | 编码 | 唯一 | 字段 |", "| --- | --- | --- | --- |"])
        for index in table.indexes:
            lines.append(
                f"| {_md(index.name)} | {_md(index.code)} | {'是' if index.unique else ''} | {_md(', '.join(index.columns))} |"
            )
    return "\n".join(lines)


def export_table_text(table: PdmTable) -> str:
    title = table.code or table.name or "未命名表"
    lines = [f"表: {title}"]
    if table.name and table.name != title:
        lines.append(f"名称: {table.name}")
    if table.comment:
        lines.append(f"备注: {table.comment}")
    lines.append("")
    for index, column in enumerate(table.columns, start=1):
        pk = " PK" if column.primary_key else ""
        length = f"({column.length})" if column.length else ""
        comment = f" - {column.comment}" if column.comment else ""
        lines.append(f"{index:>3}. {column.code} {column.data_type}{length}{pk}{comment}")
    if table.indexes:
        lines.append("")
        lines.append("索引:")
        for item in table.indexes:
            unique = " UNIQUE" if item.unique else ""
            lines.append(f"- {item.code or item.name}{unique}: {', '.join(item.columns)}")
    return "\n".join(lines)


def _local(tag: str) -> str:
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    if ":" in tag:
        tag = tag.split(":", 1)[1]
    return tag


def _iter_local(element: ET.Element, name: str):
    for child in element.iter():
        if _local(child.tag) == name:
            yield child


def _children_local(element: ET.Element, name: str):
    for child in list(element):
        if _local(child.tag) == name:
            yield child


def _child(element: ET.Element, name: str) -> ET.Element | None:
    for child in list(element):
        if _local(child.tag) == name:
            return child
    return None


def _child_text(element: ET.Element, name: str) -> str:
    child = _child(element, name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _refs_in(element: ET.Element, local_name: str) -> list[str]:
    refs: list[str] = []
    for item in _iter_local(element, local_name):
        ref = item.attrib.get("Ref")
        if ref:
            refs.append(ref)
    return refs


def _primary_key_column_refs(table_el: ET.Element) -> set[str]:
    key_refs: set[str] = set()
    direct_column_refs: set[str] = set()
    primary = _child(table_el, "PrimaryKey")
    if primary is not None:
        key_refs.update(_refs_in(primary, "Key"))
        direct_column_refs.update(_refs_in(primary, "Column"))

    result: set[str] = set(direct_column_refs)
    keys_container = _child(table_el, "Keys")
    if keys_container is not None:
        for key_el in _children_local(keys_container, "Key"):
            key_id = key_el.attrib.get("Id", "")
            if key_id in key_refs:
                result.update(_refs_in(key_el, "Column"))
    return result


def _parse_indexes(table_el: ET.Element, column_by_id: dict[str, PdmColumn]) -> list[PdmIndex]:
    indexes: list[PdmIndex] = []
    indexes_container = _child(table_el, "Indexes")
    if indexes_container is None:
        return indexes
    for index_el in _children_local(indexes_container, "Index"):
        refs = _refs_in(index_el, "Column")
        columns = [column_by_id[ref].code or column_by_id[ref].name for ref in refs if ref in column_by_id]
        unique_text = _child_text(index_el, "Unique").lower()
        indexes.append(
            PdmIndex(
                name=_child_text(index_el, "Name"),
                code=_child_text(index_el, "Code"),
                unique=unique_text in {"1", "true", "yes"},
                columns=columns,
            )
        )
    return indexes


def _md(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ")
