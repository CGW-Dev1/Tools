from __future__ import annotations

import base64
import io
import json
import mimetypes
import re
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageTk
except Exception:  # pragma: no cover - runtime fallback when Pillow is missing
    Image = None
    ImageDraw = None
    ImageTk = None

from . import __version__
from .cron_utils import CronError, PRESETS, describe_cron, next_times
from .crypto_utils import DigestRow, digest_file, digest_text, rows_to_text
from .doc_compare import DiffResult, DocumentReadError, build_document_diff, read_document_file
from .pdm_parser import PdmModel, PdmTable, export_table_markdown, export_table_text, parse_pdm
from .regex_utils import REGEX_TEMPLATES, explain_regex
from .state import StateStore
from .theme import apply_ttk_theme, palette
from .display import BRAND_FONT, MONO_FONT, UI_FONT, configure_tk_display, enable_windows_dpi_awareness, mono_font, ui_font


APP_TITLE = "全能开发工具箱"


def resource_path(relative_path: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / relative_path


class TextPane(ttk.Frame):
    def __init__(
        self,
        master: tk.Widget,
        title: str = "",
        *,
        wrap: str = "none",
        height: int = 12,
        readonly: bool = False,
    ) -> None:
        super().__init__(master)
        self.readonly = readonly
        if title:
            self.label = ttk.Label(self, text=title, style="Section.TLabel")
            self.label.grid(row=0, column=0, sticky="w", pady=(0, 6))
        else:
            self.label = None

        row = 1 if title else 0
        frame = ttk.Frame(self, style="Panel.TFrame")
        frame.grid(row=row, column=0, sticky="nsew")
        self.grid_rowconfigure(row, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.text = tk.Text(
            frame,
            wrap=wrap,
            undo=True,
            maxundo=100,
            relief="flat",
            borderwidth=0,
            highlightthickness=1,
            font=MONO_FONT,
            padx=10,
            pady=10,
            spacing1=1,
            spacing2=0,
            spacing3=1,
        )
        ybar = ttk.Scrollbar(frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=ybar.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        if wrap == "none":
            xbar = ttk.Scrollbar(frame, orient="horizontal", command=self.text.xview)
            self.text.configure(xscrollcommand=xbar.set)
            xbar.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        if readonly:
            self.text.configure(state="disabled")

    def apply_theme(self, colors: dict[str, str]) -> None:
        self.text.configure(
            background=colors["field"],
            foreground=colors["text"],
            insertbackground=colors["text"],
            selectbackground=colors["selection"],
            highlightbackground=colors["border"],
            highlightcolor=colors["accent"],
        )
        self.text.tag_configure("error", foreground=colors["error"])
        self.text.tag_configure("success", foreground=colors["success"])
        self.text.tag_configure("muted", foreground=colors["muted"])
        self.text.tag_configure("json_key", foreground=colors["json_key"])
        self.text.tag_configure("json_string", foreground=colors["json_string"])
        self.text.tag_configure("json_number", foreground=colors["json_number"])
        self.text.tag_configure("json_bool", foreground=colors["json_bool"])
        self.text.tag_configure("json_null", foreground=colors["muted"])
        self.text.tag_configure("json_punct", foreground=colors["muted"])
        self.text.tag_configure("match", background=colors["match"], foreground=colors["text"])
        self.text.tag_configure("diff_equal", foreground=colors["muted"])
        self.text.tag_configure("diff_insert", background=colors["diff_insert"], foreground=colors["text"])
        self.text.tag_configure("diff_delete", background=colors["diff_delete"], foreground=colors["text"])
        self.text.tag_configure("diff_change", background=colors["diff_change"], foreground=colors["text"])
        self.text.tag_configure("diff_header", background=colors["diff_header"], foreground=colors["text"])

    def get(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set(self, value: str) -> None:
        state = self.text.cget("state")
        if state == "disabled":
            self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        if state == "disabled":
            self.text.configure(state="disabled")

    def clear(self) -> None:
        self.set("")

    def append(self, value: str, tag: str | None = None) -> None:
        state = self.text.cget("state")
        if state == "disabled":
            self.text.configure(state="normal")
        if tag:
            self.text.insert("end", value, tag)
        else:
            self.text.insert("end", value)
        if state == "disabled":
            self.text.configure(state="disabled")

    def remove_tags(self) -> None:
        for tag in self.text.tag_names():
            self.text.tag_remove(tag, "1.0", "end")


class ToolPage(ttk.Frame):
    key = ""
    title = ""

    def __init__(self, app: "DevToolboxApp") -> None:
        super().__init__(app.content, style="TFrame")
        self.app = app
        self.text_panes: list[TextPane] = []

    def apply_theme(self, colors: dict[str, str]) -> None:
        for pane in self.text_panes:
            pane.apply_theme(colors)

    def load_state(self, data: dict[str, Any]) -> None:
        pass

    def get_state(self) -> dict[str, Any]:
        return {}

    def clear(self) -> None:
        pass

    def reset(self) -> None:
        self.clear()

    def copy_text(self, text: str) -> None:
        self.app.copy_text(text)

    def set_status(self, text: str, kind: str = "muted") -> None:
        self.app.set_status(text, kind)


class PdmGridTable(ttk.Frame):
    def __init__(
        self,
        master: tk.Widget,
        columns: list[tuple[str, str, int, str]],
        copy_owner: Any,
        *,
        empty_text: str = "暂无数据",
    ) -> None:
        super().__init__(master)
        self.columns_def = columns
        self.copy_owner = copy_owner
        self.empty_text = empty_text
        self.rows: list[tuple[Any, ...]] = []
        self.row_ids: list[str] = []
        self._row_id_to_index: dict[str, int] = {}
        self.row_tags: list[str] = []
        self.selected_row: int | None = None
        self.active_cell: tuple[int, int] | None = None
        self.row_height = 32
        self.header_height = 34
        self.body_font = ui_font(10)
        self.header_font = ui_font(10, "bold")
        self.measure_font = tkfont.Font(font=self.body_font)
        self.colors = palette(getattr(copy_owner.app, "theme_name", "dark"))
        self._resize_column_index: int | None = None
        self._resize_start_x = 0
        self._resize_start_width = 0
        self._resize_grip = 6
        self._resize_min_width = 44

        self.header = tk.Canvas(self, height=self.header_height, bd=0, highlightthickness=1, takefocus=0)
        self.body = tk.Canvas(self, bd=0, highlightthickness=1, takefocus=1)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self._yview)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self._xview)
        self.body.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)

        self.header.grid(row=0, column=0, sticky="ew")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.vbar.grid(row=1, column=1, sticky="ns")
        self.hbar.grid(row=2, column=0, sticky="ew")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header.bind("<Configure>", lambda _event: self._draw_header())
        self.header.bind("<Motion>", self._on_header_motion)
        self.header.bind("<Button-1>", self._on_header_press)
        self.header.bind("<B1-Motion>", self._on_header_drag)
        self.header.bind("<ButtonRelease-1>", self._on_header_release)
        self.header.bind("<Leave>", self._on_header_leave)
        self.body.bind("<Configure>", lambda _event: self._draw_body())
        self.body.bind("<Button-1>", self._on_left_click)
        self.body.bind("<Button-3>", self._on_right_click)
        self.body.bind("<Control-c>", self._on_copy)
        self.body.bind("<Control-C>", self._on_copy)
        self.body.bind("<MouseWheel>", self._on_mouse_wheel)
        self.apply_theme(self.colors)

    def apply_theme(self, colors: dict[str, str]) -> None:
        self.colors = colors
        bg = colors["field"]
        for canvas in (self.header, self.body):
            canvas.configure(
                bg=bg,
                highlightbackground=colors["border"],
                highlightcolor=colors["accent"],
            )
        self._draw_header()
        self._draw_body()

    def set_zoom(self, size: int) -> None:
        self.row_height = max(30, size + 20)
        self.header_height = max(32, size + 22)
        self.body_font = ui_font(size)
        self.header_font = ui_font(size, "bold")
        self.measure_font = tkfont.Font(font=self.body_font)
        self.header.configure(height=self.header_height)
        self._update_scrollregion()
        self._draw_header()
        self._draw_body()

    def set_rows(self, rows: list[tuple[Any, ...]], row_ids: list[str] | None = None, row_tags: list[str] | None = None) -> None:
        self.rows = rows
        self.row_ids = row_ids if row_ids is not None else [str(index) for index in range(len(rows))]
        self._row_id_to_index = {item_id: index for index, item_id in enumerate(self.row_ids)}
        self.row_tags = row_tags if row_tags is not None else ["" for _row in rows]
        if self.selected_row is not None and self.selected_row >= len(self.rows):
            self.selected_row = None
        if self.active_cell is not None and self.active_cell[0] >= len(self.rows):
            self.active_cell = None
        self._update_scrollregion()
        self._draw_body()

    def get_children(self) -> tuple[str, ...]:
        return tuple(self.row_ids)

    def selection(self) -> tuple[str, ...]:
        return (self._row_id(self.selected_row),) if self.selected_row is not None else ()

    def selection_set(self, item_id: str | int) -> None:
        index = self._row_index(item_id)
        if index is None:
            return
        if 0 <= index < len(self.rows):
            self.selected_row = index
            self._draw_body()

    def focus(self, item_id: str | int | None = None) -> str:
        if item_id is not None:
            self.selection_set(item_id)
        return self._row_id(self.selected_row) if self.selected_row is not None else ""

    def focus_set(self) -> None:
        self.body.focus_set()

    def exists(self, item_id: str | int) -> bool:
        index = self._row_index(item_id)
        if index is None:
            return False
        return 0 <= index < len(self.rows)

    def item(self, item_id: str | int, option: str | None = None) -> Any:
        index = self._row_index(item_id)
        if index is None or not 0 <= index < len(self.rows):
            values: tuple[Any, ...] = ()
        else:
            values = self.rows[index]
        data = {"text": "", "values": values}
        return data.get(option, "") if option else data

    def heading(self, column_id: str, option: str | None = None) -> Any:
        title = ""
        for column, text, _width, _anchor in self.columns_def:
            if column == column_id:
                title = text
                break
        data = {"text": title}
        return data.get(option, "") if option else data

    def column(self, column_id: str, option: str | None = None) -> Any:
        for column, _text, width, anchor in self.columns_def:
            if column == column_id:
                data = {"width": width, "anchor": anchor, "id": column}
                return data.get(option, "") if option else data
        return "" if option else {}

    def cget(self, option: str) -> Any:
        if option == "show":
            return "headings"
        return super().cget(option)

    def __getitem__(self, key: str) -> Any:
        if key == "columns":
            return tuple(column for column, _text, _width, _anchor in self.columns_def)
        return super().__getitem__(key)

    def identify_row(self, y: int) -> str:
        row = int(self.body.canvasy(y) // self.row_height)
        return self._row_id(row) if 0 <= row < len(self.rows) else ""

    def identify_column(self, x: int) -> str:
        target = self.body.canvasx(x)
        current = 0
        for index, (_column, _text, width, _anchor) in enumerate(self.columns_def, start=1):
            current += width
            if target < current:
                return f"#{index}"
        return ""

    def _xview(self, *args: Any) -> None:
        self.header.xview(*args)
        self.body.xview(*args)

    def _yview(self, *args: Any) -> None:
        self.body.yview(*args)
        self._draw_body()

    def _on_left_click(self, event: tk.Event) -> str:
        row_id = self.identify_row(event.y)
        column_id = self.identify_column(event.x)
        self.body.focus_set()
        if row_id:
            row_index = self._row_index(row_id)
            if row_index is None:
                return "break"
            self.selected_row = row_index
            if column_id:
                self.active_cell = (row_index, int(column_id[1:]) - 1)
                self.copy_owner._active_tree_cell = (self, row_id, column_id)
            self._draw_body()
        return "break"

    def _on_right_click(self, event: tk.Event) -> str:
        self.body.focus_set()
        return self.copy_owner._show_tree_copy_menu(event, self)

    def _on_copy(self, _event: tk.Event) -> str:
        return self.copy_owner._copy_tree_from_event(self)

    def _on_mouse_wheel(self, event: tk.Event) -> str:
        self.body.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._clamp_yview()
        self._draw_body()
        return "break"

    def _on_header_motion(self, event: tk.Event) -> str:
        if self._resize_column_index is not None or self._column_boundary_at(event.x) is not None:
            self.header.configure(cursor="sb_h_double_arrow")
        else:
            self.header.configure(cursor="")
        return "break"

    def _on_header_press(self, event: tk.Event) -> str:
        column_index = self._column_boundary_at(event.x)
        if column_index is None:
            return ""
        self._resize_column_index = column_index
        self._resize_start_x = int(self.header.canvasx(event.x))
        self._resize_start_width = self.columns_def[column_index][2]
        self.header.configure(cursor="sb_h_double_arrow")
        return "break"

    def _on_header_drag(self, event: tk.Event) -> str:
        if self._resize_column_index is None:
            return ""
        current_x = int(self.header.canvasx(event.x))
        new_width = max(self._resize_min_width, self._resize_start_width + current_x - self._resize_start_x)
        self._set_column_width(self._resize_column_index, new_width)
        return "break"

    def _on_header_release(self, _event: tk.Event) -> str:
        self._resize_column_index = None
        self._resize_start_x = 0
        self._resize_start_width = 0
        self.header.configure(cursor="")
        return "break"

    def _on_header_leave(self, _event: tk.Event) -> str:
        if self._resize_column_index is None:
            self.header.configure(cursor="")
        return "break"

    def _update_scrollregion(self) -> None:
        total_width = self._total_width()
        total_height = max(self._content_height(), self._viewport_height())
        self.header.configure(scrollregion=(0, 0, total_width, self.header_height))
        self.body.configure(scrollregion=(0, 0, total_width, total_height))

    def _draw_header(self) -> None:
        self.header.delete("all")
        colors = self.colors
        x = 0
        for _column, title, width, _anchor in self.columns_def:
            self.header.create_rectangle(
                x,
                0,
                x + width,
                self.header_height,
                fill=colors["panel_alt"],
                outline=colors["border"],
            )
            self.header.create_text(
                x + width / 2,
                self.header_height / 2,
                text=self._fit_text(title, width, self.header_font),
                anchor="center",
                fill=colors["text"],
                font=self.header_font,
            )
            x += width

    def _draw_body(self) -> None:
        self.body.delete("all")
        self._update_scrollregion()
        self._clamp_yview()
        colors = self.colors
        if not self.rows:
            self.body.create_text(
                max(20, self.body.winfo_width() / 2),
                max(20, self.body.winfo_height() / 2),
                text=self.empty_text,
                anchor="center",
                fill=colors["muted"],
                font=self.body_font,
            )
            return
        alt = colors["panel_alt"]
        x_positions = self._column_x_positions()
        first_row, last_row = self._visible_row_range()
        for row_index in range(first_row, last_row):
            values = self.rows[row_index]
            y = row_index * self.row_height
            tag = self.row_tags[row_index] if row_index < len(self.row_tags) else ""
            tag_fill = {
                "inserted": colors.get("diff_insert", colors["field"]),
                "deleted": colors.get("diff_delete", colors["field"]),
                "changed": colors.get("diff_change", colors["field"]),
            }.get(tag)
            row_fill = colors["selection"] if row_index == self.selected_row else (tag_fill if tag_fill else (alt if row_index % 2 else colors["field"]))
            for col_index, (column, _title, width, anchor) in enumerate(self.columns_def):
                x = x_positions[col_index]
                value = values[col_index] if col_index < len(values) else ""
                self.body.create_rectangle(
                    x,
                    y,
                    x + width,
                    y + self.row_height,
                    fill=row_fill,
                    outline=colors["border"],
                )
                text_anchor, text_x = self._text_position(x, width, anchor)
                self.body.create_text(
                    text_x,
                    y + self.row_height / 2,
                    text=self._fit_text(str(value), width, self.body_font),
                    anchor=text_anchor,
                    fill=colors["text"],
                    font=self.body_font,
                )
                if self.active_cell == (row_index, col_index):
                    self.body.create_rectangle(
                        x + 1,
                        y + 1,
                        x + width - 1,
                        y + self.row_height - 1,
                        outline=colors["accent"],
                        width=2,
                    )

    def _visible_row_range(self) -> tuple[int, int]:
        if not self.rows:
            return 0, 0
        top_y = min(max(0, self.body.canvasy(0)), self._max_y_offset())
        top = max(0, int(top_y // self.row_height) - 1)
        bottom_y = top_y + self._viewport_height()
        bottom = min(len(self.rows), int(bottom_y // self.row_height) + 3)
        return top, max(top, bottom)

    def _content_height(self) -> int:
        return max(self.row_height * len(self.rows), self.row_height)

    def _viewport_height(self) -> int:
        return max(1, self.body.winfo_height())

    def _max_y_offset(self) -> int:
        return max(0, self._content_height() - self._viewport_height())

    def _clamp_yview(self) -> None:
        content_height = self._content_height()
        if content_height <= 0:
            return
        current = self.body.canvasy(0)
        target = min(max(0, current), self._max_y_offset())
        if abs(current - target) > 0.5:
            self.body.yview_moveto(target / max(1, max(content_height, self._viewport_height())))

    def _column_x_positions(self) -> list[int]:
        positions: list[int] = []
        current = 0
        for _column, _title, width, _anchor in self.columns_def:
            positions.append(current)
            current += width
        return positions

    def _row_id(self, index: int | None) -> str:
        if index is None or not 0 <= index < len(self.row_ids):
            return ""
        return self.row_ids[index]

    def _row_index(self, item_id: str | int) -> int | None:
        item = str(item_id)
        if item in self._row_id_to_index:
            return self._row_id_to_index[item]
        try:
            index = int(item)
        except (TypeError, ValueError):
            return None
        return index if 0 <= index < len(self.rows) else None

    def _total_width(self) -> int:
        return sum(width for _column, _title, width, _anchor in self.columns_def)

    def _column_boundary_at(self, x: int) -> int | None:
        target = self.header.canvasx(x)
        current = 0
        for index, (_column, _title, width, _anchor) in enumerate(self.columns_def):
            current += width
            if abs(target - current) <= self._resize_grip:
                return index
        return None

    def _set_column_width(self, column_index: int, width: int) -> None:
        if not 0 <= column_index < len(self.columns_def):
            return
        column, title, _old_width, anchor = self.columns_def[column_index]
        self.columns_def[column_index] = (column, title, int(width), anchor)
        self._update_scrollregion()
        self._draw_header()
        self._draw_body()

    def _text_position(self, x: int, width: int, anchor: str) -> tuple[str, float]:
        if anchor == "w":
            return "w", x + 8
        if anchor == "e":
            return "e", x + width - 8
        return "center", x + width / 2

    def _fit_text(self, value: str, width: int, font: Any) -> str:
        available = max(12, width - 16)
        measure = tkfont.Font(font=font)
        if measure.measure(value) <= available:
            return value
        ellipsis = "..."
        low, high = 0, len(value)
        while low < high:
            mid = (low + high + 1) // 2
            candidate = value[:mid] + ellipsis
            if measure.measure(candidate) <= available:
                low = mid
            else:
                high = mid - 1
        return value[:low] + ellipsis


class JsonPage(ToolPage):
    key = "json"
    title = "JSON格式化"

    def __init__(self, app: "DevToolboxApp") -> None:
        super().__init__(app)
        self._after_id: str | None = None
        self._last_output = ""

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Button(toolbar, text="格式化", style="Accent.TButton", command=self.format_json).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="压缩", command=self.compress_json).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="校验", command=self.validate_json).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="复制结果", command=lambda: self.copy_text(self.output.get())).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="清空", command=self.clear).pack(side="left")
        self.status = ttk.Label(toolbar, text="输入JSON后自动校验与格式化", style="Status.TLabel")
        self.status.pack(side="left", padx=16)

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)
        self.input = TextPane(paned, "原始JSON", wrap="none")
        right = ttk.Frame(paned)
        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        self.output = TextPane(notebook, "格式化结果", wrap="none")
        self.text_panes.extend([self.input, self.output])
        notebook.add(self.output, text="高亮文本")
        tree_frame = ttk.Frame(notebook)
        self.tree = ttk.Treeview(tree_frame, columns=("type", "size"), show="tree headings")
        self.tree.heading("#0", text="节点")
        self.tree.heading("type", text="类型")
        self.tree.heading("size", text="大小")
        self.tree.column("#0", minwidth=260, width=420)
        self.tree.column("type", width=100, anchor="center")
        self.tree.column("size", width=80, anchor="center")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        notebook.add(tree_frame, text="层级树")
        paned.add(self.input, weight=1)
        paned.add(right, weight=1)

        self.input.text.bind("<KeyRelease>", self._schedule_auto)

    def load_state(self, data: dict[str, Any]) -> None:
        self.input.set(data.get("input", ""))
        self.output.set(data.get("output", ""))
        self._last_output = self.output.get()
        self._highlight_json()
        if self.input.get().strip():
            self._schedule_auto()

    def get_state(self) -> dict[str, Any]:
        return {"input": self.input.get(), "output": self.output.get()}

    def _schedule_auto(self, _event: tk.Event | None = None) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(450, self.format_json)

    def _parse(self) -> Any:
        raw = self.input.get().strip()
        if not raw:
            self.output.clear()
            self._clear_tree()
            self.status.configure(text="等待输入JSON")
            raise ValueError("empty")
        return json.loads(raw)

    def format_json(self) -> None:
        try:
            data = self._parse()
        except ValueError as exc:
            if str(exc) != "empty":
                self._show_json_error(exc)
            return
        except json.JSONDecodeError as exc:
            self._show_json_error(exc)
            return
        rendered = json.dumps(data, ensure_ascii=False, indent=2)
        self.output.set(rendered)
        self._last_output = rendered
        self._highlight_json()
        self._populate_tree(data)
        self.status.configure(text="JSON合法，已格式化")
        self.set_status("JSON格式化完成", "success")

    def compress_json(self) -> None:
        try:
            data = self._parse()
        except ValueError as exc:
            if str(exc) != "empty":
                self._show_json_error(exc)
            return
        except json.JSONDecodeError as exc:
            self._show_json_error(exc)
            return
        rendered = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self.output.set(rendered)
        self._last_output = rendered
        self._highlight_json()
        self._populate_tree(data)
        self.status.configure(text="JSON合法，已压缩为单行")

    def validate_json(self) -> None:
        try:
            self._parse()
        except ValueError as exc:
            if str(exc) != "empty":
                self._show_json_error(exc)
            return
        except json.JSONDecodeError as exc:
            self._show_json_error(exc)
            return
        self.status.configure(text="JSON语法校验通过")
        self.set_status("JSON语法校验通过", "success")

    def _show_json_error(self, exc: Exception) -> None:
        if isinstance(exc, json.JSONDecodeError):
            msg = f"语法错误：第 {exc.lineno} 行，第 {exc.colno} 列，{exc.msg}"
        else:
            msg = f"语法错误：{exc}"
        self.status.configure(text=msg)
        self.set_status(msg, "error")

    def _highlight_json(self) -> None:
        pane = self.output
        pane.remove_tags()
        text = pane.get()
        for match in re.finditer(r'"(?:\\.|[^"\\])*"', text):
            end = match.end()
            following = text[end:]
            tag = "json_key" if re.match(r"\s*:", following) else "json_string"
            pane.text.tag_add(tag, f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        for match in re.finditer(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])", text):
            pane.text.tag_add("json_number", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        for match in re.finditer(r"\b(?:true|false)\b", text):
            pane.text.tag_add("json_bool", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        for match in re.finditer(r"\bnull\b", text):
            pane.text.tag_add("json_null", f"1.0+{match.start()}c", f"1.0+{match.end()}c")
        for match in re.finditer(r"[\{\}\[\],:]", text):
            pane.text.tag_add("json_punct", f"1.0+{match.start()}c", f"1.0+{match.end()}c")

    def _clear_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _populate_tree(self, data: Any) -> None:
        self._clear_tree()
        root = self.tree.insert("", "end", text="root", values=(type(data).__name__, self._size(data)), open=True)
        self._insert_json_node(root, data)

    def _insert_json_node(self, parent: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                node = self.tree.insert(parent, "end", text=str(key), values=(type(child).__name__, self._size(child)))
                self._insert_json_node(node, child)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                node = self.tree.insert(parent, "end", text=f"[{index}]", values=(type(child).__name__, self._size(child)))
                self._insert_json_node(node, child)
        else:
            self.tree.insert(parent, "end", text=repr(value), values=(type(value).__name__, ""))

    @staticmethod
    def _size(value: Any) -> str:
        if isinstance(value, (dict, list)):
            return str(len(value))
        return ""

    def clear(self) -> None:
        self.input.clear()
        self.output.clear()
        self._clear_tree()
        self.status.configure(text="已清空")


class CronPage(ToolPage):
    key = "cron"
    title = "Cron表达式工具"
    MODES = ("每隔秒", "每隔分钟", "每隔小时", "每天", "每周", "每月", "每年", "自定义")
    WEEKDAY_TO_CRON = {
        "周日": "SUN",
        "周一": "MON",
        "周二": "TUE",
        "周三": "WED",
        "周四": "THU",
        "周五": "FRI",
        "周六": "SAT",
    }
    CRON_TO_WEEKDAY = {value: key for key, value in WEEKDAY_TO_CRON.items()}

    def __init__(self, app: "DevToolboxApp") -> None:
        super().__init__(app)
        self._updating = False
        self.rule_frames: dict[str, ttk.Frame] = {}
        self.custom_entries: dict[str, ttk.Entry] = {}

        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 10))
        ttk.Label(top, text="Cron表达式生成器", style="Section.TLabel").pack(side="left")
        ttk.Button(top, text="清空", command=self.clear).pack(side="right")

        body = ttk.PanedWindow(self, orient="vertical")
        body.pack(fill="both", expand=True)

        config = ttk.Frame(body)
        body.add(config, weight=0)
        left = ttk.Frame(config)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12))
        right = ttk.Frame(config)
        right.pack(side="right", fill="y")

        self.mode = tk.StringVar(value="每天")
        self.second_interval = tk.IntVar(value=10)
        self.minute_interval = tk.IntVar(value=5)
        self.hour_interval = tk.IntVar(value=1)
        self.hour = tk.IntVar(value=2)
        self.minute = tk.IntVar(value=0)
        self.second = tk.IntVar(value=0)
        self.day = tk.IntVar(value=1)
        self.month = tk.IntVar(value=1)
        self.weekday_label = tk.StringVar(value="周一")
        self.custom_fields = {
            "秒": tk.StringVar(value="0"),
            "分": tk.StringVar(value="0"),
            "时": tk.StringVar(value="2"),
            "日": tk.StringVar(value="*"),
            "月": tk.StringVar(value="*"),
            "周": tk.StringVar(value="?"),
            "年": tk.StringVar(value=""),
        }

        ttk.Label(left, text="选择执行规则", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        mode_grid = ttk.Frame(left)
        mode_grid.pack(fill="x", pady=(0, 12))
        for index, mode in enumerate(self.MODES):
            ttk.Radiobutton(
                mode_grid,
                text=mode,
                value=mode,
                variable=self.mode,
                command=self.refresh_from_config,
            ).grid(row=index // 4, column=index % 4, sticky="w", padx=(0, 18), pady=4)

        self.rule_host = ttk.Frame(left)
        self.rule_host.pack(fill="x", pady=(0, 10))
        self._build_rule_frames()

        self.mode_hint = ttk.Label(left, text="", style="Status.TLabel")
        self.mode_hint.pack(anchor="w", pady=(2, 4))
        self.field_preview = ttk.Label(left, text="", style="Status.TLabel")
        self.field_preview.pack(anchor="w", pady=(0, 4))

        ttk.Label(right, text="常用模板", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        for name, expr in PRESETS.items():
            ttk.Button(right, text=name, command=lambda e=expr: self.apply_preset(e)).pack(fill="x", pady=3)

        result = ttk.Frame(body)
        body.add(result, weight=1)
        expr_row = ttk.Frame(result)
        expr_row.pack(fill="x", pady=(0, 8))
        ttk.Label(expr_row, text="生成结果").pack(side="left", padx=(0, 8))
        self.expression = tk.StringVar(value="0 0 2 * * ?")
        entry = ttk.Entry(expr_row, textvariable=self.expression)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(expr_row, text="复制", command=lambda: self.copy_text(self.expression.get())).pack(side="left", padx=(8, 0))
        ttk.Button(expr_row, text="解析预览", command=self.parse_expression).pack(side="left", padx=(8, 0))
        self.expression.trace_add("write", lambda *_: self._expression_changed())

        lower = ttk.PanedWindow(result, orient="horizontal")
        lower.pack(fill="both", expand=True)
        explain_frame = ttk.Frame(lower)
        self.explain = TextPane(explain_frame, "中文解析", wrap="word", readonly=True)
        self.explain.pack(fill="both", expand=True)
        self.text_panes.append(self.explain)
        times_frame = ttk.Frame(lower)
        ttk.Label(times_frame, text="未来10次执行时间", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        self.times = ttk.Treeview(times_frame, columns=("time",), show="headings", height=10)
        self.times.heading("time", text="执行时间")
        self.times.column("time", anchor="w", width=260)
        self.times.pack(fill="both", expand=True)
        lower.add(explain_frame, weight=1)
        lower.add(times_frame, weight=1)

        for var in [self.second_interval, self.minute_interval, self.hour_interval, self.hour, self.minute, self.second, self.day, self.month]:
            var.trace_add("write", lambda *_: self.refresh_from_config())
        self.weekday_label.trace_add("write", lambda *_: self.refresh_from_config())
        self.refresh_from_config()

    def _build_rule_frames(self) -> None:
        frame = self._rule_frame("每隔秒")
        self._spin_inline(frame, "每", self.second_interval, 1, 59, "秒执行一次")

        frame = self._rule_frame("每隔分钟")
        self._spin_inline(frame, "每", self.minute_interval, 1, 59, "分钟执行一次")
        self._spin_inline(frame, "在第", self.second, 0, 59, "秒触发")

        frame = self._rule_frame("每隔小时")
        self._spin_inline(frame, "每", self.hour_interval, 1, 23, "小时执行一次")
        self._spin_inline(frame, "在第", self.minute, 0, 59, "分")
        self._spin_inline(frame, "", self.second, 0, 59, "秒触发")

        frame = self._rule_frame("每天")
        self._spin_inline(frame, "每天", self.hour, 0, 23, "时")
        self._spin_inline(frame, "", self.minute, 0, 59, "分")
        self._spin_inline(frame, "", self.second, 0, 59, "秒执行")

        frame = self._rule_frame("每周")
        ttk.Label(frame, text="每周").pack(side="left", padx=(0, 6))
        ttk.Combobox(
            frame,
            textvariable=self.weekday_label,
            values=list(self.WEEKDAY_TO_CRON.keys()),
            state="readonly",
            width=8,
        ).pack(side="left", padx=(0, 12))
        self._spin_inline(frame, "", self.hour, 0, 23, "时")
        self._spin_inline(frame, "", self.minute, 0, 59, "分")
        self._spin_inline(frame, "", self.second, 0, 59, "秒执行")

        frame = self._rule_frame("每月")
        self._spin_inline(frame, "每月", self.day, 1, 31, "日")
        self._spin_inline(frame, "", self.hour, 0, 23, "时")
        self._spin_inline(frame, "", self.minute, 0, 59, "分")
        self._spin_inline(frame, "", self.second, 0, 59, "秒执行")

        frame = self._rule_frame("每年")
        self._spin_inline(frame, "每年", self.month, 1, 12, "月")
        self._spin_inline(frame, "", self.day, 1, 31, "日")
        self._spin_inline(frame, "", self.hour, 0, 23, "时")
        self._spin_inline(frame, "", self.minute, 0, 59, "分")
        self._spin_inline(frame, "", self.second, 0, 59, "秒执行")

        frame = self._rule_frame("自定义")
        ttk.Label(frame, text="高级Cron字段").grid(row=0, column=0, columnspan=7, sticky="w", pady=(0, 6))
        for index, (label, var) in enumerate(self.custom_fields.items()):
            ttk.Label(frame, text=label).grid(row=1, column=index, padx=(0, 4), sticky="w")
            entry = ttk.Entry(frame, textvariable=var, width=9)
            entry.grid(row=2, column=index, padx=(0, 6), sticky="ew")
            self.custom_entries[label] = entry
            var.trace_add("write", lambda *_: self._custom_changed())

    def _rule_frame(self, mode: str) -> ttk.Frame:
        frame = ttk.Frame(self.rule_host)
        self.rule_frames[mode] = frame
        return frame

    def _spin_inline(self, parent: ttk.Frame, label: str, variable: tk.IntVar, low: int, high: int, suffix: str) -> ttk.Spinbox:
        if label:
            ttk.Label(parent, text=label).pack(side="left", padx=(0, 6))
        spinbox = ttk.Spinbox(parent, from_=low, to=high, textvariable=variable, width=6, command=self.refresh_from_config)
        spinbox.pack(side="left", padx=(0, 6))
        if suffix:
            ttk.Label(parent, text=suffix).pack(side="left", padx=(0, 12))
        return spinbox

    def _custom_changed(self) -> None:
        if self._updating or self.mode.get() != "自定义":
            return
        self.refresh_from_config()

    def refresh_from_config(self) -> None:
        if self._updating:
            return
        self._show_rule_frame()
        mode = self.mode.get()
        sec = self._int_value(self.second, 0, 0, 59)
        minute = self._int_value(self.minute, 0, 0, 59)
        hour = self._int_value(self.hour, 2, 0, 23)
        day = self._int_value(self.day, 1, 1, 31)
        month = self._int_value(self.month, 1, 1, 12)

        if mode == "每隔秒":
            interval = self._int_value(self.second_interval, 10, 1, 59)
            second_field = "*" if interval == 1 else f"*/{interval}"
            expr = f"{second_field} * * * * ?"
        elif mode == "每隔分钟":
            interval = self._int_value(self.minute_interval, 5, 1, 59)
            minute_field = "*" if interval == 1 else f"*/{interval}"
            expr = f"{sec} {minute_field} * * * ?"
        elif mode == "每隔小时":
            interval = self._int_value(self.hour_interval, 1, 1, 23)
            hour_field = "*" if interval == 1 else f"*/{interval}"
            expr = f"{sec} {minute} {hour_field} * * ?"
        elif mode == "每天":
            expr = f"{sec} {minute} {hour} * * ?"
        elif mode == "每周":
            weekday = self.WEEKDAY_TO_CRON.get(self.weekday_label.get(), "MON")
            expr = f"{sec} {minute} {hour} ? * {weekday}"
        elif mode == "每月":
            expr = f"{sec} {minute} {hour} {day} * ?"
        elif mode == "每年":
            expr = f"{sec} {minute} {hour} {day} {month} ?"
        else:
            parts = [self.custom_fields[key].get().strip() for key in ("秒", "分", "时", "日", "月", "周")]
            year = self.custom_fields["年"].get().strip()
            expr = " ".join(parts + ([year] if year else []))
            self._set_expression(expr, sync_fields=False)
            return
        self._set_expression(expr, sync_fields=True)

    def apply_preset(self, expression: str) -> None:
        self._updating = True
        self.expression.set(expression)
        self._sync_custom_fields_from_expression(expression)
        self._sync_controls_from_expression(expression)
        self._updating = False
        self._show_rule_frame()
        self._update_generator_preview(expression)
        self.parse_expression()

    def _set_expression(self, expression: str, *, sync_fields: bool) -> None:
        self._updating = True
        self.expression.set(expression)
        if sync_fields:
            self._sync_custom_fields_from_expression(expression)
        self._updating = False
        self._update_generator_preview(expression)
        self.parse_expression()

    def _expression_changed(self) -> None:
        if self._updating:
            return
        expression = self.expression.get()
        self._updating = True
        self._sync_custom_fields_from_expression(expression)
        self._sync_controls_from_expression(expression)
        self._updating = False
        self._show_rule_frame()
        self._update_generator_preview(expression)
        self.parse_expression()

    def _show_rule_frame(self) -> None:
        mode = self.mode.get()
        for frame in self.rule_frames.values():
            frame.pack_forget()
        frame = self.rule_frames.get(mode)
        if frame is not None:
            frame.pack(fill="x")

    def _update_generator_preview(self, expression: str) -> None:
        hints = {
            "每隔秒": "规则：按秒间隔循环执行，适合高频本地调试任务。",
            "每隔分钟": "规则：按分钟间隔循环执行，可指定触发秒。",
            "每隔小时": "规则：按小时间隔循环执行，可指定触发分秒。",
            "每天": "规则：每天固定时间执行。",
            "每周": "规则：每周指定星期和时间执行。",
            "每月": "规则：每月指定日期和时间执行。",
            "每年": "规则：每年指定月份、日期和时间执行。",
            "自定义": "规则：直接编辑Cron字段，适合复杂表达式。",
        }
        self.mode_hint.configure(text=hints.get(self.mode.get(), ""))
        parts = self._expression_parts(expression)
        if not parts:
            self.field_preview.configure(text="字段：等待生成有效Cron表达式")
            return
        labels = ("秒", "分", "时", "日", "月", "周", "年")
        self.field_preview.configure(text="字段：" + "  ".join(f"{label}={value}" for label, value in zip(labels, parts)))

    def _sync_custom_fields_from_expression(self, expression: str) -> None:
        parts = self._expression_parts(expression)
        if not parts:
            return
        for key, value in zip(("秒", "分", "时", "日", "月", "周"), parts[:6]):
            self.custom_fields[key].set(value)
        self.custom_fields["年"].set(parts[6] if len(parts) > 6 else "")

    def _sync_controls_from_expression(self, expression: str) -> None:
        parts = self._expression_parts(expression)
        if not parts:
            return
        sec, minute, hour, day, month, weekday = parts[:6]
        matched_mode = "自定义"
        if (sec == "*" or sec.startswith("*/")) and minute == "*" and hour == "*" and day == "*" and month == "*" and weekday == "?":
            matched_mode = "每隔秒"
            self._set_int(self.second_interval, "1" if sec == "*" else sec[2:], 10, 1, 59)
        elif (minute == "*" or minute.startswith("*/")) and hour == "*" and day == "*" and month == "*" and weekday == "?" and self._is_int(sec, 0, 59):
            matched_mode = "每隔分钟"
            self._set_int(self.minute_interval, "1" if minute == "*" else minute[2:], 5, 1, 59)
            self._set_int(self.second, sec, 0, 0, 59)
        elif (hour == "*" or hour.startswith("*/")) and day == "*" and month == "*" and weekday == "?" and self._is_int(sec, 0, 59) and self._is_int(minute, 0, 59):
            matched_mode = "每隔小时"
            self._set_int(self.hour_interval, "1" if hour == "*" else hour[2:], 1, 1, 23)
            self._set_int(self.second, sec, 0, 0, 59)
            self._set_int(self.minute, minute, 0, 0, 59)
        elif day == "*" and month == "*" and weekday == "?" and self._is_int(sec, 0, 59) and self._is_int(minute, 0, 59) and self._is_int(hour, 0, 23):
            matched_mode = "每天"
            self._set_int(self.second, sec, 0, 0, 59)
            self._set_int(self.minute, minute, 0, 0, 59)
            self._set_int(self.hour, hour, 2, 0, 23)
        elif day == "?" and month == "*" and weekday in self.CRON_TO_WEEKDAY and self._is_int(sec, 0, 59) and self._is_int(minute, 0, 59) and self._is_int(hour, 0, 23):
            matched_mode = "每周"
            self._set_int(self.second, sec, 0, 0, 59)
            self._set_int(self.minute, minute, 0, 0, 59)
            self._set_int(self.hour, hour, 2, 0, 23)
            self.weekday_label.set(self.CRON_TO_WEEKDAY[weekday])
        elif month == "*" and weekday == "?" and self._is_int(sec, 0, 59) and self._is_int(minute, 0, 59) and self._is_int(hour, 0, 23) and self._is_int(day, 1, 31):
            matched_mode = "每月"
            self._set_int(self.second, sec, 0, 0, 59)
            self._set_int(self.minute, minute, 0, 0, 59)
            self._set_int(self.hour, hour, 2, 0, 23)
            self._set_int(self.day, day, 1, 1, 31)
        elif weekday == "?" and self._is_int(sec, 0, 59) and self._is_int(minute, 0, 59) and self._is_int(hour, 0, 23) and self._is_int(day, 1, 31) and self._is_int(month, 1, 12):
            matched_mode = "每年"
            self._set_int(self.second, sec, 0, 0, 59)
            self._set_int(self.minute, minute, 0, 0, 59)
            self._set_int(self.hour, hour, 2, 0, 23)
            self._set_int(self.day, day, 1, 1, 31)
            self._set_int(self.month, month, 1, 1, 12)
        self.mode.set(matched_mode)

    def _expression_parts(self, expression: str) -> list[str]:
        parts = expression.strip().split()
        if len(parts) == 5:
            parts = ["0", *parts]
        if len(parts) in {6, 7}:
            return parts
        return []

    def _int_value(self, variable: tk.IntVar, default: int, low: int, high: int) -> int:
        try:
            value = int(variable.get())
        except Exception:
            return default
        return min(max(value, low), high)

    def _set_int(self, variable: tk.IntVar, raw: str, default: int, low: int, high: int) -> None:
        try:
            value = int(raw)
        except Exception:
            value = default
        variable.set(min(max(value, low), high))

    def _is_int(self, raw: str, low: int, high: int) -> bool:
        try:
            value = int(raw)
        except Exception:
            return False
        return low <= value <= high

    def parse_expression(self) -> None:
        expr = self.expression.get().strip()
        if not expr:
            self.explain.set("")
            self._set_times([])
            return
        try:
            description = describe_cron(expr)
            times = next_times(expr, 10, datetime.now())
        except CronError as exc:
            self.explain.set(f"表达式错误：{exc}")
            self._set_times([])
            self.set_status(f"Cron错误：{exc}", "error")
            return
        self.explain.set(description)
        self._set_times(times)
        self.set_status("Cron生成完成", "success")

    def _set_times(self, times: list[datetime]) -> None:
        for item in self.times.get_children():
            self.times.delete(item)
        for dt in times:
            self.times.insert("", "end", values=(dt.strftime("%Y-%m-%d %H:%M:%S"),))

    def load_state(self, data: dict[str, Any]) -> None:
        expression = data.get("expression")
        if expression:
            self._updating = True
            self.expression.set(expression)
            self._sync_custom_fields_from_expression(expression)
            self._sync_controls_from_expression(expression)
            self._updating = False
            self._show_rule_frame()
            self._update_generator_preview(expression)
            self.parse_expression()

    def get_state(self) -> dict[str, Any]:
        return {"expression": self.expression.get()}

    def clear(self) -> None:
        self._updating = True
        self.mode.set("每天")
        self.second_interval.set(10)
        self.minute_interval.set(5)
        self.hour_interval.set(1)
        self.hour.set(2)
        self.minute.set(0)
        self.second.set(0)
        self.day.set(1)
        self.month.set(1)
        self.weekday_label.set("周一")
        self._updating = False
        self.refresh_from_config()


class Base64Page(ToolPage):
    key = "base64"
    title = "Base64编解码"

    def __init__(self, app: "DevToolboxApp") -> None:
        super().__init__(app)
        self._decode_after: str | None = None
        self._image_bytes: bytes | None = None
        self._image_format = "png"
        self._preview_image: Any = None

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        text_tab = ttk.Frame(notebook)
        image_tab = ttk.Frame(notebook)
        notebook.add(text_tab, text="文字")
        notebook.add(image_tab, text="图片")

        text_toolbar = ttk.Frame(text_tab)
        text_toolbar.pack(fill="x", pady=(0, 10))
        ttk.Button(text_toolbar, text="编码", style="Accent.TButton", command=self.encode_text).pack(side="left", padx=(0, 8))
        ttk.Button(text_toolbar, text="解码", command=self.decode_text).pack(side="left", padx=(0, 8))
        ttk.Button(text_toolbar, text="复制结果", command=lambda: self.copy_text(self.text_output.get())).pack(side="left", padx=(0, 8))
        ttk.Button(text_toolbar, text="清空", command=self.clear_text).pack(side="left")
        text_paned = ttk.PanedWindow(text_tab, orient="horizontal")
        text_paned.pack(fill="both", expand=True)
        self.text_input = TextPane(text_paned, "输入", wrap="word")
        self.text_output = TextPane(text_paned, "输出", wrap="word")
        self.text_panes.extend([self.text_input, self.text_output])
        text_paned.add(self.text_input, weight=1)
        text_paned.add(self.text_output, weight=1)

        image_toolbar = ttk.Frame(image_tab)
        image_toolbar.pack(fill="x", pady=(0, 10))
        ttk.Button(image_toolbar, text="选择图片", style="Accent.TButton", command=self.choose_image).pack(side="left", padx=(0, 8))
        ttk.Button(image_toolbar, text="Base64预览", command=self.decode_image).pack(side="left", padx=(0, 8))
        ttk.Button(image_toolbar, text="保存图片", command=self.save_image).pack(side="left", padx=(0, 8))
        ttk.Button(image_toolbar, text="复制Base64", command=lambda: self.copy_text(self.image_base64.get())).pack(side="left", padx=(0, 8))
        ttk.Button(image_toolbar, text="清空", command=self.clear_image).pack(side="left")
        self.image_status = ttk.Label(image_toolbar, text="可选择本地图片，或粘贴图片Base64后预览", style="Status.TLabel")
        self.image_status.pack(side="left", padx=16)

        image_paned = ttk.PanedWindow(image_tab, orient="horizontal")
        image_paned.pack(fill="both", expand=True)
        left = ttk.Frame(image_paned)
        ttk.Label(left, text="图片预览", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        self.preview = ttk.Label(left, text="暂无图片", anchor="center", style="Panel.TLabel")
        self.preview.pack(fill="both", expand=True)
        self.image_base64 = TextPane(image_paned, "Base64字符串", wrap="word")
        self.text_panes.append(self.image_base64)
        image_paned.add(left, weight=1)
        image_paned.add(self.image_base64, weight=1)
        self.image_base64.text.bind("<KeyRelease>", self._schedule_decode_image)

    def encode_text(self) -> None:
        raw = self.text_input.get()
        encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        self.text_output.set(encoded)
        self.set_status("文本Base64编码完成", "success")

    def decode_text(self) -> None:
        raw = self.text_input.get().strip()
        try:
            decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        except Exception as exc:
            self.text_output.set(f"解码失败：{exc}")
            self.set_status("Base64文本解码失败", "error")
            return
        self.text_output.set(decoded)
        self.set_status("文本Base64解码完成", "success")

    def choose_image(self) -> None:
        path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[
                ("图片文件", "*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        data = Path(path).read_bytes()
        mime = mimetypes.guess_type(path)[0] or "image/png"
        encoded = base64.b64encode(data).decode("ascii")
        self.image_base64.set(f"data:{mime};base64,{encoded}")
        self._image_bytes = data
        self._image_format = Path(path).suffix.lstrip(".") or "png"
        self._show_preview(data)
        self.image_status.configure(text=f"已载入 {Path(path).name}，{len(data):,} bytes")
        self.set_status("图片已转换为Base64", "success")

    def _schedule_decode_image(self, _event: tk.Event | None = None) -> None:
        if self._decode_after:
            self.after_cancel(self._decode_after)
        self._decode_after = self.after(700, self.decode_image)

    def _strip_data_uri(self, text: str) -> tuple[str, str]:
        text = text.strip()
        image_format = "png"
        if text.startswith("data:"):
            header, _, payload = text.partition(",")
            text = payload
            if "/" in header:
                image_format = header.split("/", 1)[1].split(";", 1)[0]
        return text, image_format

    def decode_image(self) -> None:
        raw, image_format = self._strip_data_uri(self.image_base64.get())
        if not raw:
            return
        try:
            data = base64.b64decode(raw, validate=False)
            self._show_preview(data)
        except Exception as exc:
            self.image_status.configure(text=f"图片解码失败：{exc}")
            self.set_status("图片Base64解码失败", "error")
            return
        self._image_bytes = data
        self._image_format = image_format or "png"
        self.image_status.configure(text=f"图片解码成功，{len(data):,} bytes")
        self.set_status("图片Base64解码完成", "success")

    def _show_preview(self, data: bytes) -> None:
        if Image is None or ImageTk is None:
            self.preview.configure(text="Pillow未安装，无法预览图片")
            return
        try:
            image = Image.open(io.BytesIO(data))
            image.thumbnail((520, 520))
            self._preview_image = ImageTk.PhotoImage(image)
            self.preview.configure(image=self._preview_image, text="")
        except Exception as exc:
            self.preview.configure(image="", text=f"无法预览图片：{exc}")

    def save_image(self) -> None:
        if not self._image_bytes:
            messagebox.showinfo(APP_TITLE, "没有可保存的图片。")
            return
        ext = self._image_format.lower().replace("jpeg", "jpg")
        path = filedialog.asksaveasfilename(
            title="保存图片",
            defaultextension=f".{ext}",
            filetypes=[("图片文件", f"*.{ext}"), ("所有文件", "*.*")],
        )
        if not path:
            return
        Path(path).write_bytes(self._image_bytes)
        self.set_status(f"图片已保存：{path}", "success")

    def clear_text(self) -> None:
        self.text_input.clear()
        self.text_output.clear()

    def clear_image(self) -> None:
        self.image_base64.clear()
        self._image_bytes = None
        self.preview.configure(image="", text="暂无图片")
        self.image_status.configure(text="已清空")

    def clear(self) -> None:
        self.clear_text()
        self.clear_image()

    def load_state(self, data: dict[str, Any]) -> None:
        self.text_input.set(data.get("text_input", ""))
        self.text_output.set(data.get("text_output", ""))
        self.image_base64.set(data.get("image_base64", ""))

    def get_state(self) -> dict[str, Any]:
        image_text = self.image_base64.get()
        if len(image_text) > 2_000_000:
            image_text = ""
        return {
            "text_input": self.text_input.get(),
            "text_output": self.text_output.get(),
            "image_base64": image_text,
        }


class RegexPage(ToolPage):
    key = "regex"
    title = "正则表达式工具"

    def __init__(self, app: "DevToolboxApp") -> None:
        super().__init__(app)
        self._after_id: str | None = None
        self.ignore_case = tk.BooleanVar(value=False)
        self.multiline = tk.BooleanVar(value=True)
        self.global_match = tk.BooleanVar(value=True)

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text="表达式").pack(side="left", padx=(0, 8))
        self.pattern = tk.StringVar(value="")
        pattern_entry = ttk.Entry(toolbar, textvariable=self.pattern)
        pattern_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Checkbutton(toolbar, text="忽略大小写", variable=self.ignore_case, command=self.refresh).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(toolbar, text="全局", variable=self.global_match, command=self.refresh).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(toolbar, text="多行", variable=self.multiline, command=self.refresh).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="复制表达式", command=lambda: self.copy_text(self.pattern.get())).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="清空", command=self.clear).pack(side="left")

        template_row = ttk.Frame(self)
        template_row.pack(fill="x", pady=(0, 10))
        ttk.Label(template_row, text="模板").pack(side="left", padx=(0, 8))
        self.template_name = tk.StringVar(value=list(REGEX_TEMPLATES.keys())[0])
        box = ttk.Combobox(template_row, textvariable=self.template_name, values=list(REGEX_TEMPLATES.keys()), state="readonly", width=24)
        box.pack(side="left", padx=(0, 8))
        ttk.Button(template_row, text="套用模板", style="Accent.TButton", command=self.apply_template).pack(side="left")

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)
        self.sample = TextPane(paned, "测试文本", wrap="word")
        right = ttk.Frame(paned)
        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        self.text_panes.append(self.sample)
        matches_frame = ttk.Frame(notebook)
        self.matches = ttk.Treeview(matches_frame, columns=("span", "value"), show="headings")
        self.matches.heading("span", text="位置")
        self.matches.heading("value", text="匹配内容")
        self.matches.column("span", width=120, anchor="center")
        self.matches.column("value", width=360)
        self.matches.pack(fill="both", expand=True)
        notebook.add(matches_frame, text="匹配列表")
        explain_frame = ttk.Frame(notebook)
        self.explanation = TextPane(explain_frame, "正则释义", wrap="word", readonly=True)
        self.explanation.pack(fill="both", expand=True)
        self.text_panes.append(self.explanation)
        notebook.add(explain_frame, text="释义")
        paned.add(self.sample, weight=1)
        paned.add(right, weight=1)

        self.pattern.trace_add("write", lambda *_: self.refresh())
        self.sample.text.bind("<KeyRelease>", self._schedule_refresh)

    def apply_template(self) -> None:
        self.pattern.set(REGEX_TEMPLATES[self.template_name.get()])
        self.refresh()

    def _schedule_refresh(self, _event: tk.Event | None = None) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(250, self.refresh)

    def refresh(self) -> None:
        pattern = self.pattern.get()
        text = self.sample.get()
        self.sample.remove_tags()
        self.explanation.set(explain_regex(pattern))
        for item in self.matches.get_children():
            self.matches.delete(item)
        if not pattern:
            return
        flags = 0
        if self.ignore_case.get():
            flags |= re.IGNORECASE
        if self.multiline.get():
            flags |= re.MULTILINE
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            self.explanation.set(f"正则错误：{exc}\n\n{explain_regex(pattern)}")
            self.set_status(f"正则错误：{exc}", "error")
            return
        matches = list(regex.finditer(text)) if self.global_match.get() else ([m] if (m := regex.search(text)) else [])
        for match in matches:
            start, end = match.span()
            if start == end:
                continue
            self.sample.text.tag_add("match", f"1.0+{start}c", f"1.0+{end}c")
            value = match.group(0).replace("\n", "\\n")
            if len(value) > 160:
                value = value[:157] + "..."
            self.matches.insert("", "end", values=(f"{start}-{end}", value))
        self.set_status(f"匹配数量：{len(matches)}", "success")

    def load_state(self, data: dict[str, Any]) -> None:
        self.pattern.set(data.get("pattern", ""))
        self.sample.set(data.get("sample", ""))
        self.ignore_case.set(bool(data.get("ignore_case", False)))
        self.multiline.set(bool(data.get("multiline", True)))
        self.global_match.set(bool(data.get("global_match", True)))
        self.refresh()

    def get_state(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern.get(),
            "sample": self.sample.get(),
            "ignore_case": self.ignore_case.get(),
            "multiline": self.multiline.get(),
            "global_match": self.global_match.get(),
        }

    def clear(self) -> None:
        self.pattern.set("")
        self.sample.clear()
        self.explanation.clear()
        for item in self.matches.get_children():
            self.matches.delete(item)


class CryptoPage(ToolPage):
    key = "crypto"
    title = "加密哈希"

    def __init__(self, app: "DevToolboxApp") -> None:
        super().__init__(app)
        self._after_id: str | None = None
        self._suppress_hash = False
        self._file_hash_job = 0
        self.text_rows: list[DigestRow] = []
        self.file_rows: list[DigestRow] = []
        self.file_path = tk.StringVar(value="")
        self.encoding = tk.StringVar(value="UTF-8")
        self.hmac_secret = tk.StringVar(value="")
        self.uppercase = tk.BooleanVar(value=False)
        self.show_secret = tk.BooleanVar(value=False)
        self._active_tree_cell: tuple[Any, str, str] | None = None
        self._copy_menu_tree: Any | None = None
        self._copy_menu_cell: tuple[Any, str, str] | None = None
        self.copy_menu = tk.Menu(self, tearoff=0)
        self.copy_menu.add_command(label="复制单元格", command=self.copy_menu_cell)
        self.copy_menu.add_command(label="复制整行", command=self.copy_menu_selection)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        text_tab = ttk.Frame(notebook)
        file_tab = ttk.Frame(notebook)
        notebook.add(text_tab, text="文本摘要")
        notebook.add(file_tab, text="文件摘要")

        text_toolbar = ttk.Frame(text_tab)
        text_toolbar.pack(fill="x", pady=(0, 10))
        text_top = ttk.Frame(text_toolbar)
        text_top.pack(fill="x", pady=(0, 6))
        text_bottom = ttk.Frame(text_toolbar)
        text_bottom.pack(fill="x")

        ttk.Button(text_top, text="计算", style="Accent.TButton", command=self.refresh_text_hash).pack(side="left", padx=(0, 8))
        ttk.Button(text_top, text="复制选中Hex", command=lambda: self.copy_selected_hex(self.text_table, self.text_rows)).pack(side="left", padx=(0, 8))
        ttk.Button(text_top, text="复制全部", command=lambda: self.copy_rows(self.text_rows)).pack(side="left", padx=(0, 8))
        ttk.Button(text_top, text="导出结果", command=lambda: self.export_rows(self.text_rows, "文本摘要结果")).pack(side="left", padx=(0, 8))
        ttk.Button(text_top, text="清空", command=self.clear_text).pack(side="left")

        ttk.Label(text_bottom, text="编码").pack(side="left", padx=(0, 6))
        self.encoding_box = ttk.Combobox(text_bottom, textvariable=self.encoding, values=("UTF-8", "GB18030", "UTF-16LE", "UTF-16BE"), state="readonly", width=12)
        self.encoding_box.pack(side="left", padx=(0, 12))
        self.encoding_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_text_hash())
        ttk.Checkbutton(text_bottom, text="Hex大写", variable=self.uppercase, command=self.refresh_all).pack(side="left", padx=(0, 12))
        ttk.Label(text_bottom, text="HMAC密钥").pack(side="left", padx=(0, 6))
        self.secret_entry = ttk.Entry(text_bottom, textvariable=self.hmac_secret, show="*", width=28)
        self.secret_entry.pack(side="left", padx=(0, 8))
        ttk.Checkbutton(text_bottom, text="显示密钥", variable=self.show_secret, command=self._toggle_secret_visibility).pack(side="left", padx=(0, 12))
        self.text_status = ttk.Label(text_bottom, text="输入文本后自动计算MD5、SHA、SHA3、BLAKE2", style="Status.TLabel")
        self.text_status.pack(side="left")

        text_paned = ttk.PanedWindow(text_tab, orient="horizontal")
        text_paned.pack(fill="both", expand=True)
        self.text_input = TextPane(text_paned, "输入文本", wrap="word")
        text_result_frame = ttk.Frame(text_paned)
        self.text_table = self._create_result_table(text_result_frame)
        self.text_table.pack(fill="both", expand=True)
        text_paned.add(self.text_input, weight=1)
        text_paned.add(text_result_frame, weight=3)
        self.text_panes.append(self.text_input)
        self.text_input.text.bind("<KeyRelease>", self._schedule_text_hash)
        self.hmac_secret.trace_add("write", lambda *_args: None if self._suppress_hash else self._schedule_text_hash())

        file_toolbar = ttk.Frame(file_tab)
        file_toolbar.pack(fill="x", pady=(0, 10))
        file_top = ttk.Frame(file_toolbar)
        file_top.pack(fill="x", pady=(0, 6))
        file_bottom = ttk.Frame(file_toolbar)
        file_bottom.pack(fill="x")
        ttk.Button(file_top, text="选择文件", style="Accent.TButton", command=self.choose_file).pack(side="left", padx=(0, 8))
        ttk.Button(file_top, text="计算文件", command=self.refresh_file_hash).pack(side="left", padx=(0, 8))
        ttk.Button(file_top, text="复制选中Hex", command=lambda: self.copy_selected_hex(self.file_table, self.file_rows)).pack(side="left", padx=(0, 8))
        ttk.Button(file_top, text="复制全部", command=lambda: self.copy_rows(self.file_rows)).pack(side="left", padx=(0, 8))
        ttk.Button(file_top, text="导出结果", command=lambda: self.export_rows(self.file_rows, "文件摘要结果")).pack(side="left", padx=(0, 8))
        ttk.Button(file_top, text="清空", command=self.clear_file).pack(side="left")
        self.file_info = ttk.Label(file_bottom, text="未选择文件", style="Status.TLabel")
        self.file_info.pack(side="left", fill="x", expand=True)
        file_result_frame = ttk.Frame(file_tab)
        file_result_frame.pack(fill="both", expand=True)
        self.file_table = self._create_result_table(file_result_frame)
        self.file_table.pack(fill="both", expand=True)

    def _create_result_table(self, master: tk.Widget) -> PdmGridTable:
        return PdmGridTable(
            master,
            [
                ("kind", "类型", 70, "center"),
                ("algorithm", "算法", 110, "center"),
                ("bits", "位数", 58, "center"),
                ("hex", "Hex结果", 420, "center"),
                ("base64", "Base64结果", 250, "center"),
            ],
            self,
            empty_text="输入文本或选择文件后显示摘要结果",
        )

    def apply_theme(self, colors: dict[str, str]) -> None:
        super().apply_theme(colors)
        if hasattr(self, "text_table"):
            self.text_table.apply_theme(colors)
        if hasattr(self, "file_table"):
            self.file_table.apply_theme(colors)

    def _toggle_secret_visibility(self) -> None:
        self.secret_entry.configure(show="" if self.show_secret.get() else "*")

    def _schedule_text_hash(self, _event: tk.Event | None = None) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(300, self.refresh_text_hash)

    def refresh_all(self) -> None:
        self.refresh_text_hash()
        if self.file_path.get():
            self.refresh_file_hash()

    def refresh_text_hash(self) -> None:
        self._after_id = None
        try:
            self.text_rows = digest_text(
                self.text_input.get(),
                encoding_label=self.encoding.get(),
                uppercase=self.uppercase.get(),
                hmac_secret=self.hmac_secret.get(),
            )
        except UnicodeEncodeError as exc:
            self.text_status.configure(text=f"编码失败：{exc}")
            self.set_status("文本编码失败", "error")
            return
        self._set_table_rows(self.text_table, self.text_rows)
        hmac_count = 4 if self.hmac_secret.get() else 0
        self.text_status.configure(text=f"已生成{12 + hmac_count}个摘要结果")
        self.set_status("文本摘要计算完成", "success")

    def choose_file(self) -> None:
        path = filedialog.askopenfilename(title="选择要计算摘要的文件", filetypes=[("所有文件", "*.*")])
        if not path:
            return
        self.file_path.set(path)
        size = Path(path).stat().st_size
        self.file_info.configure(text=f"{Path(path).name}，{size:,}bytes")
        self.refresh_file_hash()

    def refresh_file_hash(self) -> None:
        path = self.file_path.get()
        if not path:
            self.set_status("请先选择文件", "warning")
            return
        self._file_hash_job += 1
        job_id = self._file_hash_job
        uppercase = self.uppercase.get()
        hmac_secret = self.hmac_secret.get()
        encoding = self.encoding.get()
        self.file_info.configure(text=f"正在计算：{Path(path).name}...")
        self.set_status("正在计算文件摘要")

        def worker() -> None:
            try:
                rows = digest_file(
                    path,
                    uppercase=uppercase,
                    hmac_secret=hmac_secret,
                    hmac_encoding_label=encoding,
                )
                error: Exception | None = None
            except Exception as exc:
                rows = []
                error = exc
            self.after(0, lambda: self._file_hash_finished(job_id, path, rows, error))

        threading.Thread(target=worker, daemon=True).start()

    def _file_hash_finished(self, job_id: int, path: str, rows: list[DigestRow], error: Exception | None) -> None:
        if job_id != self._file_hash_job or path != self.file_path.get():
            return
        if error is not None:
            self.file_info.configure(text=f"文件摘要计算失败：{error}")
            self.set_status("文件摘要计算失败", "error")
            return
        self.file_rows = rows
        self._set_table_rows(self.file_table, rows)
        size = Path(path).stat().st_size if Path(path).exists() else 0
        self.file_info.configure(text=f"{Path(path).name}，{size:,}bytes，已生成{len(rows)}个摘要结果")
        self.set_status("文件摘要计算完成", "success")

    def _set_table_rows(self, table: PdmGridTable, rows: list[DigestRow]) -> None:
        table.set_rows([(row.kind, row.algorithm, row.bits, row.hex_digest, row.base64_digest) for row in rows])

    def copy_selected_hex(self, table: PdmGridTable, rows: list[DigestRow]) -> None:
        selection = table.selection()
        if not selection:
            self.set_status("请先选择一条摘要结果", "warning")
            return
        index = table._row_index(selection[0])
        if index is None or not 0 <= index < len(rows):
            self.set_status("请先选择一条摘要结果", "warning")
            return
        self.copy_text(rows[index].hex_digest)

    def copy_rows(self, rows: list[DigestRow]) -> None:
        if not rows:
            self.set_status("暂无可复制的摘要结果", "warning")
            return
        self.copy_text(rows_to_text(rows))

    def export_rows(self, rows: list[DigestRow], title: str) -> None:
        if not rows:
            self.set_status("暂无可导出的摘要结果", "warning")
            return
        path = filedialog.asksaveasfilename(
            title=f"导出{title}",
            initialfile=f"{title}.txt",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(rows_to_text(rows), encoding="utf-8")
        self.set_status(f"已导出：{path}", "success")

    def clear_text(self) -> None:
        self.text_input.clear()
        self.text_rows = []
        self.text_table.set_rows([])
        self.text_status.configure(text="已清空")

    def clear_file(self) -> None:
        self._file_hash_job += 1
        self.file_path.set("")
        self.file_rows = []
        self.file_table.set_rows([])
        self.file_info.configure(text="未选择文件")

    def clear(self) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None
        self._suppress_hash = True
        self.hmac_secret.set("")
        self._suppress_hash = False
        self.clear_text()
        self.clear_file()

    def load_state(self, data: dict[str, Any]) -> None:
        self.text_input.set(data.get("text", ""))
        self.encoding.set(data.get("encoding", "UTF-8"))
        self.hmac_secret.set("")
        self.uppercase.set(bool(data.get("uppercase", False)))
        if self.text_input.get() or self.hmac_secret.get():
            self._schedule_text_hash()

    def get_state(self) -> dict[str, Any]:
        text = self.text_input.get()
        return {
            "text": text if len(text) <= 800_000 else "",
            "encoding": self.encoding.get(),
            "uppercase": self.uppercase.get(),
        }

    def copy_menu_selection(self) -> None:
        if self._copy_menu_tree is not None:
            self.copy_tree_selection(self._copy_menu_tree)

    def copy_menu_cell(self) -> None:
        if self._copy_menu_cell is not None:
            self.copy_tree_cell(*self._copy_menu_cell)

    def copy_tree_cell(self, tree: Any, item_id: str, column_id: str) -> str:
        content = self._tree_cell_text(tree, item_id, column_id)
        if content is None:
            self.set_status("请先选择要复制的单元格", "warning")
            return ""
        self.copy_text(content)
        return content

    def copy_tree_selection(self, tree: Any) -> str:
        content = self._tree_selection_text(tree)
        if not content:
            self.set_status("请先选择要复制的行", "warning")
            return ""
        self.copy_text(content)
        return content

    def _copy_tree_from_event(self, tree: Any) -> str:
        selected = list(tree.selection())
        if len(selected) <= 1 and self._active_tree_cell is not None:
            active_tree, item_id, column_id = self._active_tree_cell
            if active_tree is tree and tree.exists(item_id):
                if not selected or item_id in selected:
                    self.copy_tree_cell(tree, item_id, column_id)
                    return "break"
        self.copy_tree_selection(tree)
        return "break"

    def _show_tree_copy_menu(self, event: tk.Event, tree: Any) -> str:
        row_id = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        if row_id:
            tree.selection_set(row_id)
            tree.focus(row_id)
        if row_id and column_id:
            self._active_tree_cell = (tree, row_id, column_id)
        tree.focus_set()
        self._copy_menu_tree = tree
        self._copy_menu_cell = (tree, row_id, column_id) if row_id and column_id else None
        self.copy_menu.entryconfigure(0, state="normal" if self._copy_menu_cell else "disabled")
        try:
            self.copy_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.copy_menu.grab_release()
        return "break"

    def _tree_cell_text(self, tree: Any, item_id: str, column_id: str) -> str | None:
        if not item_id or not tree.exists(item_id):
            return None
        try:
            column_index = int(column_id[1:]) - 1
        except (ValueError, TypeError):
            return None
        values = list(tree.item(item_id, "values"))
        if not 0 <= column_index < len(values):
            return None
        return str(values[column_index])

    def _tree_selection_text(self, tree: Any) -> str:
        selected = list(tree.selection())
        if not selected:
            focus = tree.focus()
            selected = [focus] if focus else []
        if not selected:
            return ""
        columns = list(tree["columns"])
        headers = [str(tree.heading(column, "text") or column) for column in columns]
        lines = ["\t".join(headers)]
        for item_id in selected:
            lines.append("\t".join(str(value) for value in tree.item(item_id, "values")))
        return "\n".join(lines)


class DocumentComparePage(ToolPage):
    key = "diff"
    title = "文档对比"

    def __init__(self, app: "DevToolboxApp") -> None:
        super().__init__(app)
        self._after_id: str | None = None
        self._last_result: DiffResult | None = None
        self.ignore_case = tk.BooleanVar(value=False)
        self.collapse_whitespace = tk.BooleanVar(value=False)
        self._active_tree_cell: tuple[Any, str, str] | None = None
        self._copy_menu_tree: Any | None = None
        self._copy_menu_cell: tuple[Any, str, str] | None = None
        self.copy_menu = tk.Menu(self, tearoff=0)
        self.copy_menu.add_command(label="复制单元格", command=self.copy_menu_cell)
        self.copy_menu.add_command(label="复制整行", command=self.copy_menu_selection)

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 10))
        action_row = ttk.Frame(toolbar)
        action_row.pack(fill="x", pady=(0, 6))
        option_row = ttk.Frame(toolbar)
        option_row.pack(fill="x")

        ttk.Button(action_row, text="导入文档1", style="Accent.TButton", command=lambda: self.load_file("left")).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="导入文档2", style="Accent.TButton", command=lambda: self.load_file("right")).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="粘贴文档1", command=lambda: self.paste_clipboard("left")).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="粘贴文档2", command=lambda: self.paste_clipboard("right")).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="交换文档", command=self.swap_documents).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="开始对比", command=self.compare).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="清空", command=self.clear).pack(side="left")

        ttk.Checkbutton(option_row, text="忽略大小写", variable=self.ignore_case, command=self.compare).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(option_row, text="压缩空白", variable=self.collapse_whitespace, command=self.compare).pack(side="left", padx=(0, 12))
        ttk.Button(option_row, text="导出HTML", command=self.export_html).pack(side="left", padx=(0, 8))
        ttk.Button(option_row, text="导出文本", command=self.export_text).pack(side="left", padx=(0, 8))
        ttk.Button(option_row, text="复制结果", command=self.copy_result).pack(side="left", padx=(0, 8))
        self.result_status = ttk.Label(option_row, text="可粘贴文本或导入文件后对比", style="Status.TLabel")
        self.result_status.pack(side="left", padx=8)

        main_paned = ttk.PanedWindow(self, orient="vertical")
        main_paned.pack(fill="both", expand=True)

        input_frame = ttk.Frame(main_paned)
        input_paned = ttk.PanedWindow(input_frame, orient="horizontal")
        input_paned.pack(fill="both", expand=True)
        left_frame = ttk.Frame(input_paned)
        right_frame = ttk.Frame(input_paned)
        self.left_info = ttk.Label(left_frame, text="文档1：粘贴或导入", style="Status.TLabel")
        self.left_info.pack(anchor="w", pady=(0, 6))
        self.right_info = ttk.Label(right_frame, text="文档2：粘贴或导入", style="Status.TLabel")
        self.right_info.pack(anchor="w", pady=(0, 6))
        self.left_text = TextPane(left_frame, "文档1", wrap="none")
        self.right_text = TextPane(right_frame, "文档2", wrap="none")
        self.left_text.pack(fill="both", expand=True)
        self.right_text.pack(fill="both", expand=True)
        input_paned.add(left_frame, weight=1)
        input_paned.add(right_frame, weight=1)

        result_frame = ttk.Frame(main_paned)
        result_tabs = ttk.Notebook(result_frame)
        result_tabs.pack(fill="both", expand=True)
        preview_frame = ttk.Frame(result_tabs)
        self.preview = TextPane(preview_frame, "差异预览", wrap="none", readonly=True)
        self.preview.pack(fill="both", expand=True)
        result_tabs.add(preview_frame, text="差异预览")

        list_frame = ttk.Frame(result_tabs)
        self.diff_rows = PdmGridTable(
            list_frame,
            [
                ("kind", "类型", 90, "center"),
                ("left", "文档1行", 110, "center"),
                ("right", "文档2行", 110, "center"),
                ("old", "文档1内容", 500, "center"),
                ("new", "文档2内容", 500, "center"),
            ],
            self,
            empty_text="暂无差异",
        )
        self.diff_rows.pack(fill="both", expand=True)
        result_tabs.add(list_frame, text="差异列表")

        main_paned.add(input_frame, weight=3)
        main_paned.add(result_frame, weight=2)
        self.text_panes.extend([self.left_text, self.right_text, self.preview])

        self.left_text.text.bind("<KeyRelease>", self._schedule_compare)
        self.right_text.text.bind("<KeyRelease>", self._schedule_compare)

    def apply_theme(self, colors: dict[str, str]) -> None:
        super().apply_theme(colors)
        if hasattr(self, "diff_rows"):
            self.diff_rows.apply_theme(colors)

    def load_file(self, side: str) -> None:
        path = filedialog.askopenfilename(
            title="选择对比文档",
            filetypes=[
                ("常用文档", "*.txt;*.md;*.markdown;*.json;*.xml;*.csv;*.log;*.docx"),
                ("代码文件", "*.py;*.js;*.ts;*.java;*.cs;*.cpp;*.h;*.sql;*.yml;*.yaml;*.ini;*.properties"),
                ("Word文档", "*.docx"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            content = read_document_file(path)
        except (OSError, DocumentReadError, UnicodeError) as exc:
            messagebox.showerror(APP_TITLE, f"文档读取失败：{exc}")
            self.set_status("文档读取失败", "error")
            return
        target = self.left_text if side == "left" else self.right_text
        label = self.left_info if side == "left" else self.right_info
        prefix = "文档1" if side == "left" else "文档2"
        target.set(content)
        label.configure(text=f"{prefix}：{Path(path).name}，{len(content):,}字符")
        self.set_status(f"已导入：{Path(path).name}", "success")
        self._schedule_compare()

    def paste_clipboard(self, side: str) -> None:
        try:
            content = self.clipboard_get()
        except tk.TclError:
            self.set_status("剪贴板没有文本内容", "warning")
            return
        target = self.left_text if side == "left" else self.right_text
        label = self.left_info if side == "left" else self.right_info
        prefix = "文档1" if side == "left" else "文档2"
        target.set(content)
        label.configure(text=f"{prefix}：来自剪贴板，{len(content):,}字符")
        self.set_status("剪贴板文本已粘贴", "success")
        self._schedule_compare()

    def swap_documents(self) -> None:
        left = self.left_text.get()
        right = self.right_text.get()
        self.left_text.set(right)
        self.right_text.set(left)
        left_info = self.left_info.cget("text")
        right_info = self.right_info.cget("text")
        self.left_info.configure(text=str(right_info).replace("文档2", "文档1", 1))
        self.right_info.configure(text=str(left_info).replace("文档1", "文档2", 1))
        self.compare()

    def _schedule_compare(self, _event: tk.Event | None = None) -> None:
        self._last_result = None
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(600, self.compare)

    def compare(self) -> None:
        self._after_id = None
        left = self.left_text.get()
        right = self.right_text.get()
        self._last_result = build_document_diff(
            left,
            right,
            ignore_case=self.ignore_case.get(),
            collapse_whitespace=self.collapse_whitespace.get(),
        )
        self.render_result(self._last_result)
        status_kind = "success" if self._last_result.equal else "warning"
        self.result_status.configure(text=self._last_result.summary)
        self.set_status(self._last_result.summary, status_kind)

    def render_result(self, result: DiffResult) -> None:
        self.preview.clear()
        self.preview.remove_tags()
        for segment in result.segments:
            self.preview.append(segment.text, segment.tag)
        rows: list[tuple[Any, ...]] = []
        row_tags: list[str] = []
        for row in result.rows:
            tag = {"新增": "inserted", "删除": "deleted", "修改": "changed"}.get(row.kind, "")
            rows.append((row.kind, row.left_range, row.right_range, row.left_text, row.right_text))
            row_tags.append(tag)
        self.diff_rows.set_rows(rows, row_tags=row_tags)

    def export_html(self) -> None:
        result = self.ensure_result()
        if result is None:
            return
        path = filedialog.asksaveasfilename(
            title="导出对比结果",
            initialfile="文档对比结果.html",
            defaultextension=".html",
            filetypes=[("HTML文件", "*.html"), ("所有文件", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(result.html_export, encoding="utf-8")
        self.set_status(f"已导出：{path}", "success")

    def export_text(self) -> None:
        result = self.ensure_result()
        if result is None:
            return
        path = filedialog.asksaveasfilename(
            title="导出对比结果",
            initialfile="文档对比结果.txt",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return
        Path(path).write_text(result.text_export, encoding="utf-8")
        self.set_status(f"已导出：{path}", "success")

    def copy_result(self) -> None:
        result = self.ensure_result()
        if result is not None:
            self.copy_text(result.text_export)

    def ensure_result(self) -> DiffResult | None:
        if self._last_result is None:
            self.compare()
        return self._last_result

    def load_state(self, data: dict[str, Any]) -> None:
        self.left_text.set(data.get("left", ""))
        self.right_text.set(data.get("right", ""))
        self.ignore_case.set(bool(data.get("ignore_case", False)))
        self.collapse_whitespace.set(bool(data.get("collapse_whitespace", False)))
        if self.left_text.get() or self.right_text.get():
            self._schedule_compare()

    def get_state(self) -> dict[str, Any]:
        def cached(value: str) -> str:
            return value if len(value) <= 800_000 else ""

        return {
            "left": cached(self.left_text.get()),
            "right": cached(self.right_text.get()),
            "ignore_case": self.ignore_case.get(),
            "collapse_whitespace": self.collapse_whitespace.get(),
        }

    def clear(self) -> None:
        if self._after_id:
            self.after_cancel(self._after_id)
            self._after_id = None
        self.left_text.clear()
        self.right_text.clear()
        self.preview.clear()
        self.diff_rows.set_rows([])
        self.left_info.configure(text="文档1：粘贴或导入")
        self.right_info.configure(text="文档2：粘贴或导入")
        self.result_status.configure(text="已清空")
        self._last_result = None

    def copy_menu_selection(self) -> None:
        if self._copy_menu_tree is not None:
            self.copy_tree_selection(self._copy_menu_tree)

    def copy_menu_cell(self) -> None:
        if self._copy_menu_cell is not None:
            self.copy_tree_cell(*self._copy_menu_cell)

    def copy_tree_cell(self, tree: Any, item_id: str, column_id: str) -> str:
        content = self._tree_cell_text(tree, item_id, column_id)
        if content is None:
            self.set_status("请先选择要复制的单元格", "warning")
            return ""
        self.copy_text(content)
        return content

    def copy_tree_selection(self, tree: Any) -> str:
        content = self._tree_selection_text(tree)
        if not content:
            self.set_status("请先选择要复制的行", "warning")
            return ""
        self.copy_text(content)
        return content

    def _copy_tree_from_event(self, tree: Any) -> str:
        selected = list(tree.selection())
        if len(selected) <= 1 and self._active_tree_cell is not None:
            active_tree, item_id, column_id = self._active_tree_cell
            if active_tree is tree and tree.exists(item_id):
                if not selected or item_id in selected:
                    self.copy_tree_cell(tree, item_id, column_id)
                    return "break"
        self.copy_tree_selection(tree)
        return "break"

    def _show_tree_copy_menu(self, event: tk.Event, tree: Any) -> str:
        row_id = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        if row_id:
            tree.selection_set(row_id)
            tree.focus(row_id)
        if row_id and column_id:
            self._active_tree_cell = (tree, row_id, column_id)
        tree.focus_set()
        self._copy_menu_tree = tree
        self._copy_menu_cell = (tree, row_id, column_id) if row_id and column_id else None
        self.copy_menu.entryconfigure(0, state="normal" if self._copy_menu_cell else "disabled")
        try:
            self.copy_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.copy_menu.grab_release()
        return "break"

    def _tree_cell_text(self, tree: Any, item_id: str, column_id: str) -> str | None:
        if not item_id or not tree.exists(item_id):
            return None
        try:
            column_index = int(column_id[1:]) - 1
        except (ValueError, TypeError):
            return None
        values = list(tree.item(item_id, "values"))
        if not 0 <= column_index < len(values):
            return None
        return str(values[column_index])

    def _tree_selection_text(self, tree: Any) -> str:
        selected = list(tree.selection())
        if not selected:
            focus = tree.focus()
            selected = [focus] if focus else []
        if not selected:
            return ""
        columns = list(tree["columns"])
        headers = [str(tree.heading(column, "text") or column) for column in columns]
        lines = ["\t".join(headers)]
        for item_id in selected:
            lines.append("\t".join(str(value) for value in tree.item(item_id, "values")))
        return "\n".join(lines)


@dataclass
class OpenedPdm:
    id: str
    path: str
    model: PdmModel


class PdmPage(ToolPage):
    key = "pdm"
    title = "PDM数据库查看器"

    def __init__(self, app: "DevToolboxApp") -> None:
        super().__init__(app)
        self.opened_pdms: list[OpenedPdm] = []
        self.opened_by_path: dict[str, str] = {}
        self.table_tabs: dict[str, dict[str, Any]] = {}
        self.selected_pdm_id: str | None = None
        self.home_scope_ids: list[str | None] = [None]
        self._pdm_counter = 0
        self._loading_paths: set[str] = set()
        self._copy_menu_tree: Any | None = None
        self._copy_menu_cell: tuple[Any, str, str] | None = None
        self._active_tree_cell: tuple[Any, str, str] | None = None
        self._home_search_after_id: str | None = None
        self.zoom = tk.IntVar(value=10)
        self.search = tk.StringVar(value="")
        self.home_scope = tk.StringVar(value="全部PDM")
        self.copy_menu = tk.Menu(self, tearoff=0)
        self.copy_menu.add_command(label="复制单元格", command=self.copy_menu_cell)
        self.copy_menu.add_command(label="复制整行", command=self.copy_menu_selection)

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Button(toolbar, text="打开PDM", style="Accent.TButton", command=self.open_file).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="清空PDM", command=self.clear).pack(side="left", padx=(0, 8))
        ttk.Label(toolbar, text="缩放").pack(side="left", padx=(12, 4))
        ttk.Spinbox(toolbar, from_=9, to=16, textvariable=self.zoom, width=5, command=self.apply_zoom).pack(side="left")
        self.pdm_status = ttk.Label(toolbar, text="加载本地.pdm文件后查看表结构", style="Status.TLabel")
        self.pdm_status.pack(side="left", padx=16)

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned)
        ttk.Label(left, text="PDM文件", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True)
        self.pdm_tree = ttk.Treeview(tree_frame, columns=("name", "count"), show="tree headings")
        self.pdm_tree.heading("#0", text="文件/表", anchor="center")
        self.pdm_tree.heading("name", text="名称", anchor="center")
        self.pdm_tree.heading("count", text="数量", anchor="center")
        self.pdm_tree.column("#0", width=230, anchor="w")
        self.pdm_tree.column("name", width=170, anchor="center")
        self.pdm_tree.column("count", width=58, anchor="center")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.pdm_tree.yview)
        self.pdm_tree.configure(yscrollcommand=tree_scroll.set)
        self.pdm_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.pdm_tree.bind("<<TreeviewSelect>>", self._on_pdm_tree_select)
        self.pdm_tree.bind("<<TreeviewOpen>>", self._on_pdm_tree_open)
        self._enable_tree_copy(self.pdm_tree)

        right = ttk.Frame(paned)
        self.detail_tabs = ttk.Notebook(right)
        self.detail_tabs.pack(fill="both", expand=True)
        self._build_home_tab()

        paned.add(left, weight=1)
        paned.add(right, weight=3)

        self.search.trace_add("write", lambda *_: self._schedule_refresh_home())
        self.zoom.trace_add("write", lambda *_: self.apply_zoom())

    def apply_theme(self, colors: dict[str, str]) -> None:
        super().apply_theme(colors)
        if hasattr(self, "home_tables"):
            self.home_tables.apply_theme(colors)
        for info in self.table_tabs.values():
            fields = info.get("fields")
            if hasattr(fields, "apply_theme"):
                fields.apply_theme(colors)

    def open_file(self) -> None:
        paths = filedialog.askopenfilenames(title="选择PowerDesignerPDM文件", filetypes=[("PDM文件", "*.pdm"), ("XML文件", "*.xml"), ("所有文件", "*.*")])
        if not paths:
            return
        self.open_paths(paths)

    def _build_home_tab(self) -> None:
        self.home_tab = ttk.Frame(self.detail_tabs)
        home_toolbar = ttk.Frame(self.home_tab)
        home_toolbar.pack(fill="x", pady=(0, 10))
        filter_row = ttk.Frame(home_toolbar)
        filter_row.pack(fill="x", pady=(0, 6))
        action_row = ttk.Frame(home_toolbar)
        action_row.pack(fill="x")

        ttk.Label(filter_row, text="范围").pack(side="left", padx=(0, 6))
        self.home_scope_combo = ttk.Combobox(filter_row, textvariable=self.home_scope, values=("全部PDM",), state="readonly", width=30)
        self.home_scope_combo.pack(side="left", padx=(0, 12))
        self.home_scope_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_home())
        ttk.Label(filter_row, text="搜索").pack(side="left", padx=(0, 6))
        ttk.Entry(filter_row, textvariable=self.search).pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.home_summary = ttk.Label(filter_row, text="未加载PDM", style="Status.TLabel")
        self.home_summary.pack(side="left")

        ttk.Button(action_row, text="打开表", command=self.open_home_selection).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="复制单元格", command=lambda: self.copy_active_tree_cell(self.home_tables)).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="复制选中行", command=lambda: self.copy_tree_selection(self.home_tables)).pack(side="left", padx=(0, 8))

        table_frame = ttk.Frame(self.home_tab)
        table_frame.pack(fill="both", expand=True)
        self.home_tables = PdmGridTable(
            table_frame,
            [
                ("pdm", "PDM", 190, "center"),
                ("code", "表编码", 220, "center"),
                ("name", "表名", 220, "center"),
                ("fields", "字段", 80, "center"),
                ("comment", "备注", 420, "center"),
            ],
            self,
        )
        self.home_tables.pack(fill="both", expand=True)
        self.home_tables.body.bind("<Double-1>", self.open_home_selection, add="+")
        self.home_tables.body.bind("<Return>", self.open_home_selection, add="+")
        self.detail_tabs.add(self.home_tab, text="主页")

    def open_paths(self, paths: tuple[str, ...] | list[str], *, restored: bool = False) -> None:
        pending: list[str] = []
        for raw_path in paths:
            path = str(raw_path)
            key = self._path_key(path)
            if key in self.opened_by_path or key in self._loading_paths:
                continue
            pending.append(path)
            self._loading_paths.add(key)
        if not pending:
            if paths and not restored:
                self.set_status("选择的PDM已在左侧列表中", "success")
            return
        self.pdm_status.configure(text=f"正在解析{len(pending)}个PDM...")
        self.set_status("正在解析PDM文件")

        def worker() -> None:
            results: list[tuple[str, PdmModel | None, Exception | None]] = []
            for path in pending:
                try:
                    results.append((path, parse_pdm(path), None))
                except Exception as exc:
                    results.append((path, None, exc))
            self.after(0, lambda: self._pdm_loaded_batch(results, restored=restored))

        threading.Thread(target=worker, daemon=True).start()

    def _pdm_loaded_batch(self, results: list[tuple[str, PdmModel | None, Exception | None]], *, restored: bool) -> None:
        first_loaded_id: str | None = None
        errors: list[str] = []
        for path, model, exc in results:
            key = self._path_key(path)
            self._loading_paths.discard(key)
            if exc is not None or model is None:
                errors.append(f"{Path(path).name}: {exc}")
                continue
            if key in self.opened_by_path:
                continue
            self._pdm_counter += 1
            pdm_id = f"pdm{self._pdm_counter}"
            opened = OpenedPdm(id=pdm_id, path=path, model=model)
            self.opened_pdms.append(opened)
            self.opened_by_path[key] = pdm_id
            first_loaded_id = first_loaded_id or pdm_id

        self.refresh_pdm_tree()
        self.refresh_scope_options()
        if first_loaded_id:
            self.show_home(first_loaded_id)
        else:
            self.refresh_home()

        if errors:
            message = "；".join(errors[:2])
            self.pdm_status.configure(text=f"部分解析失败：{message}")
            self.set_status(f"PDM解析失败：{message}", "error")
            if not restored:
                messagebox.showwarning(APP_TITLE, "以下PDM解析失败：\n" + "\n".join(errors))
        elif self.opened_pdms:
            total_tables = sum(len(item.model.tables) for item in self.opened_pdms)
            self.pdm_status.configure(text=f"已打开{len(self.opened_pdms)}个PDM，{total_tables}张表")
            self.set_status("PDM加载完成", "success")
        else:
            self.pdm_status.configure(text="未加载PDM")

    def refresh_pdm_tree(self) -> None:
        current = self.pdm_tree.selection()
        selected = current[0] if current else ""
        for item in self.pdm_tree.get_children():
            self.pdm_tree.delete(item)
        for opened in self.opened_pdms:
            root_iid = f"pdm:{opened.id}"
            self.pdm_tree.insert("", "end", iid=root_iid, text=Path(opened.path).name, values=(opened.model.name, len(opened.model.tables)), open=False)
            if opened.model.tables:
                self.pdm_tree.insert(root_iid, "end", iid=f"dummy:{opened.id}", text="展开加载表...", values=("", ""))
        if selected and self.pdm_tree.exists(selected):
            self.pdm_tree.selection_set(selected)
            self.pdm_tree.focus(selected)
        elif self.opened_pdms:
            root_iid = f"pdm:{self.opened_pdms[-1].id}"
            self.pdm_tree.selection_set(root_iid)
            self.pdm_tree.focus(root_iid)

    def _on_pdm_tree_open(self, _event: tk.Event) -> None:
        item_id = self.pdm_tree.focus()
        if not item_id.startswith("pdm:"):
            return
        self._populate_pdm_tree_node(item_id.split(":", 1)[1])

    def _populate_pdm_tree_node(self, pdm_id: str) -> None:
        root_iid = f"pdm:{pdm_id}"
        if not self.pdm_tree.exists(root_iid):
            return
        children = self.pdm_tree.get_children(root_iid)
        if children and not any(str(child).startswith("dummy:") for child in children):
            return
        for child in children:
            self.pdm_tree.delete(child)
        opened = self.get_pdm(pdm_id)
        if opened is None:
            return
        for index, table in enumerate(opened.model.tables):
            self.pdm_tree.insert(root_iid, "end", iid=self._table_iid(opened.id, index), text=table.code or table.name, values=(table.name, len(table.columns)))

    def refresh_scope_options(self) -> None:
        current_scope = self.current_scope_id()
        labels = ["全部PDM"]
        ids: list[str | None] = [None]
        for opened in self.opened_pdms:
            name = Path(opened.path).name
            if opened.model.name and opened.model.name != Path(opened.path).stem:
                name = f"{name} - {opened.model.name}"
            labels.append(name)
            ids.append(opened.id)
        self.home_scope_ids = ids
        self.home_scope_combo.configure(values=labels)
        target = current_scope if current_scope in ids else self.selected_pdm_id
        index = ids.index(target) if target in ids else 0
        self.home_scope_combo.current(index)
        self.home_scope.set(labels[index])

    def _schedule_refresh_home(self) -> None:
        if self._home_search_after_id:
            self.after_cancel(self._home_search_after_id)
        self._home_search_after_id = self.after(180, self._run_scheduled_refresh_home)

    def _run_scheduled_refresh_home(self) -> None:
        self._home_search_after_id = None
        self.refresh_home()

    def refresh_home(self) -> None:
        scope_id = self.current_scope_id()
        query = self.search.get().strip().lower()
        table_rows: list[tuple[Any, ...]] = []
        row_ids: list[str] = []
        rows = 0
        for opened in self.opened_pdms:
            if scope_id and opened.id != scope_id:
                continue
            for index, table in enumerate(opened.model.tables):
                if query and query not in self._table_haystack(table):
                    continue
                row_ids.append(self._home_iid(opened.id, index))
                table_rows.append(
                    (
                        Path(opened.path).name,
                        table.code or table.name,
                        table.name,
                        len(table.columns),
                        table.comment,
                    )
                )
                rows += 1
        self.home_tables.set_rows(table_rows, row_ids)
        if not self.opened_pdms:
            self.home_summary.configure(text="未加载PDM")
        elif query:
            self.home_summary.configure(text=f"匹配{rows}张表")
        else:
            self.home_summary.configure(text=f"显示{rows}张表")

    def _on_pdm_tree_select(self, _event: tk.Event) -> None:
        selection = self.pdm_tree.selection()
        if not selection:
            return
        item_id = selection[0]
        if item_id.startswith("pdm:"):
            pdm_id = item_id.split(":", 1)[1]
            self.show_home(pdm_id)
            return
        parsed = self._parse_table_iid(item_id)
        if parsed:
            pdm_id, table_index = parsed
            self.open_table_tab(pdm_id, table_index)

    def show_home(self, pdm_id: str | None = None) -> None:
        if pdm_id:
            self.selected_pdm_id = pdm_id
            if pdm_id in self.home_scope_ids:
                self.home_scope_combo.current(self.home_scope_ids.index(pdm_id))
        self.detail_tabs.select(self.home_tab)
        self.refresh_home()
        opened = self.get_pdm(pdm_id) if pdm_id else None
        if opened:
            self.set_status(f"主页：{Path(opened.path).name}")

    def open_home_selection(self, event: tk.Event | None = None) -> None:
        if event is not None and hasattr(event, "y"):
            row_id = self.home_tables.identify_row(event.y)
            if row_id:
                self.home_tables.selection_set(row_id)
        selection = self.home_tables.selection()
        if not selection:
            return
        parsed = self._parse_home_iid(selection[0])
        if parsed:
            self.open_table_tab(*parsed)

    def open_table_tab(self, pdm_id: str, table_index: int) -> None:
        opened = self.get_pdm(pdm_id)
        if opened is None or not 0 <= table_index < len(opened.model.tables):
            return
        tab_key = self.table_tab_key(pdm_id, table_index)
        existing = self.table_tabs.get(tab_key)
        if existing:
            self.detail_tabs.select(existing["frame"])
            return

        table = opened.model.tables[table_index]
        frame = ttk.Frame(self.detail_tabs)
        header = ttk.Frame(frame)
        header.pack(fill="x", pady=(0, 8))
        top_row = ttk.Frame(header)
        top_row.pack(fill="x", pady=(0, 6))
        action_row = ttk.Frame(header)
        action_row.pack(fill="x")

        ttk.Label(top_row, text=table.code or table.name or "未命名表", style="Section.TLabel").pack(side="left", padx=(0, 16))
        ttk.Label(top_row, text="字段搜索").pack(side="left", padx=(0, 6))
        field_search = tk.StringVar(value="")
        ttk.Entry(top_row, textvariable=field_search, width=42).pack(side="left", fill="x", expand=True, padx=(0, 8))

        ttk.Button(action_row, text="导出Markdown", command=lambda key=tab_key: self.export_markdown(key)).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="导出文本", command=lambda key=tab_key: self.export_text(key)).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="复制单元格", command=lambda key=tab_key: self.copy_active_table_cell(key)).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="复制选中行", command=lambda key=tab_key: self.copy_field_selection(key)).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="复制导出", command=lambda key=tab_key: self.copy_current_export(key)).pack(side="left", padx=(0, 8))
        ttk.Button(action_row, text="关闭标签", command=lambda key=tab_key: self.close_current_tab(key)).pack(side="right")

        detail = ttk.Notebook(frame)
        detail.pack(fill="both", expand=True)

        fields_frame = ttk.Frame(detail)
        fields_tree = self._create_fields_tree(fields_frame)
        fields_tree.pack(fill="both", expand=True)
        detail.add(fields_frame, text="字段列表")

        indexes_frame = ttk.Frame(detail)
        indexes_tree = self._create_indexes_tree(indexes_frame)
        indexes_tree.pack(side="left", fill="both", expand=True)
        self._enable_tree_copy(indexes_tree)
        index_scroll = ttk.Scrollbar(indexes_frame, orient="vertical", command=indexes_tree.yview)
        indexes_tree.configure(yscrollcommand=index_scroll.set)
        index_scroll.pack(side="right", fill="y")
        detail.add(indexes_frame, text="索引")

        preview_frame = ttk.Frame(detail)
        preview_text = TextPane(preview_frame, "导出预览", wrap="none")
        preview_text.pack(fill="both", expand=True)
        preview_text.apply_theme(palette(self.app.theme_name))
        self.text_panes.append(preview_text)
        detail.add(preview_frame, text="导出预览")

        self.table_tabs[tab_key] = {
            "frame": frame,
            "pdm": opened,
            "pdm_id": pdm_id,
            "table_index": table_index,
            "table": table,
            "field_search": field_search,
            "fields": fields_tree,
            "indexes": indexes_tree,
            "preview": preview_text,
        }
        field_search.trace_add("write", lambda *_args, key=tab_key: self.refresh_table_fields(key))
        self.refresh_table_fields(tab_key)
        self.refresh_table_indexes(tab_key)
        preview_text.set(export_table_markdown(table))

        self.detail_tabs.add(frame, text=self._tab_title(table))
        self.detail_tabs.select(frame)
        self.set_status(f"已打开表：{table.code or table.name}", "success")

    def _create_fields_tree(self, master: tk.Widget) -> PdmGridTable:
        return PdmGridTable(
            master,
            [
                ("seq", "序号", 60, "center"),
                ("code", "字段名", 180, "center"),
                ("name", "名称", 180, "center"),
                ("type", "类型", 140, "center"),
                ("length", "长度", 70, "center"),
                ("pk", "主键", 70, "center"),
                ("comment", "备注", 420, "center"),
            ],
            self,
        )

    def _create_indexes_tree(self, master: tk.Widget) -> ttk.Treeview:
        indexes = ttk.Treeview(master, columns=("name", "code", "unique", "columns"), show="headings")
        for heading, text, width, anchor in [
            ("name", "名称", 180, "center"),
            ("code", "编码", 180, "center"),
            ("unique", "唯一", 70, "center"),
            ("columns", "字段", 360, "w"),
        ]:
            indexes.heading(heading, text=text, anchor="center")
            indexes.column(heading, width=width, anchor=anchor)
        return indexes

    def refresh_table_fields(self, tab_key: str) -> None:
        info = self.table_tabs.get(tab_key)
        if not info:
            return
        tree = info["fields"]
        query = info["field_search"].get().strip().lower()
        table: PdmTable = info["table"]
        rows: list[tuple[Any, ...]] = []
        for index, column in enumerate(table.columns, start=1):
            haystack = " ".join([column.code, column.name, column.data_type, column.length, column.comment]).lower()
            if query and query not in haystack:
                continue
            rows.append(
                (
                    index,
                    column.code,
                    column.name,
                    column.data_type,
                    column.length,
                    "是" if column.primary_key else "",
                    column.comment,
                ),
            )
        tree.set_rows(rows)

    def refresh_table_indexes(self, tab_key: str) -> None:
        info = self.table_tabs.get(tab_key)
        if not info:
            return
        tree: ttk.Treeview = info["indexes"]
        for item in tree.get_children():
            tree.delete(item)
        table: PdmTable = info["table"]
        for index_item in table.indexes:
            tree.insert("", "end", values=(index_item.name, index_item.code, "是" if index_item.unique else "", ", ".join(index_item.columns)))

    def export_markdown(self, tab_key: str | None = None) -> None:
        info = self.current_table_info(tab_key)
        if not info:
            messagebox.showinfo(APP_TITLE, "请先选择一张表。")
            return
        table: PdmTable = info["table"]
        content = export_table_markdown(table)
        preview = info.get("preview")
        if preview:
            preview.set(content)
        self._save_export(table, content, ".md", "Markdown文件")

    def export_text(self, tab_key: str | None = None) -> None:
        info = self.current_table_info(tab_key)
        if not info:
            messagebox.showinfo(APP_TITLE, "请先选择一张表。")
            return
        table: PdmTable = info["table"]
        content = export_table_text(table)
        preview = info.get("preview")
        if preview:
            preview.set(content)
        self._save_export(table, content, ".txt", "文本文件")

    def copy_current_export(self, tab_key: str | None = None) -> None:
        info = self.current_table_info(tab_key)
        if not info:
            messagebox.showinfo(APP_TITLE, "请先选择一张表。")
            return
        preview = info.get("preview")
        content = preview.get() if preview else export_table_markdown(info["table"])
        self.copy_text(content)

    def copy_field_selection(self, tab_key: str) -> None:
        info = self.table_tabs.get(tab_key)
        if not info:
            return
        self.copy_tree_selection(info["fields"])

    def copy_active_table_cell(self, tab_key: str) -> str:
        info = self.table_tabs.get(tab_key)
        if not info:
            return ""
        return self.copy_active_tree_cell(info["fields"])

    def copy_active_tree_cell(self, tree: ttk.Treeview) -> str:
        if self._active_tree_cell is None:
            self.set_status("请先点击要复制的单元格", "warning")
            return ""
        active_tree, item_id, column_id = self._active_tree_cell
        if active_tree is not tree or not tree.exists(item_id):
            self.set_status("请先点击要复制的单元格", "warning")
            return ""
        return self.copy_tree_cell(tree, item_id, column_id)

    def copy_menu_selection(self) -> None:
        if self._copy_menu_tree is not None:
            self.copy_tree_selection(self._copy_menu_tree)

    def copy_menu_cell(self) -> None:
        if self._copy_menu_cell is not None:
            self.copy_tree_cell(*self._copy_menu_cell)

    def copy_tree_cell(self, tree: ttk.Treeview, item_id: str, column_id: str) -> str:
        content = self._tree_cell_text(tree, item_id, column_id)
        if content is None:
            self.set_status("请先选择要复制的单元格", "warning")
            return ""
        self.copy_text(content)
        return content

    def copy_tree_selection(self, tree: ttk.Treeview) -> str:
        content = self._tree_selection_text(tree)
        if not content:
            self.set_status("请先选择要复制的行", "warning")
            return ""
        self.copy_text(content)
        return content

    def _enable_tree_copy(self, tree: ttk.Treeview) -> None:
        tree.bind("<Control-c>", lambda _event, target=tree: self._copy_tree_from_event(target))
        tree.bind("<Control-C>", lambda _event, target=tree: self._copy_tree_from_event(target))
        tree.bind("<ButtonRelease-1>", lambda event, target=tree: self._remember_tree_cell(event, target), add="+")
        tree.bind("<Button-3>", lambda event, target=tree: self._show_tree_copy_menu(event, target))

    def _copy_tree_from_event(self, tree: ttk.Treeview) -> str:
        selected = list(tree.selection())
        if len(selected) <= 1 and self._active_tree_cell is not None:
            active_tree, item_id, column_id = self._active_tree_cell
            if active_tree is tree and tree.exists(item_id):
                if not selected or item_id in selected:
                    self.copy_tree_cell(tree, item_id, column_id)
                    return "break"
        self.copy_tree_selection(tree)
        return "break"

    def _remember_tree_cell(self, event: tk.Event, tree: ttk.Treeview) -> None:
        row_id = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        if row_id and column_id:
            self._active_tree_cell = (tree, row_id, column_id)

    def _show_tree_copy_menu(self, event: tk.Event, tree: ttk.Treeview) -> str:
        row_id = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        if row_id and row_id not in tree.selection():
            tree.selection_set(row_id)
            tree.focus(row_id)
        if row_id and column_id:
            self._active_tree_cell = (tree, row_id, column_id)
        tree.focus_set()
        self._copy_menu_tree = tree
        self._copy_menu_cell = (tree, row_id, column_id) if row_id and column_id else None
        self.copy_menu.entryconfigure(0, state="normal" if self._copy_menu_cell else "disabled")
        try:
            self.copy_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.copy_menu.grab_release()
        return "break"

    def _tree_cell_text(self, tree: ttk.Treeview, item_id: str, column_id: str) -> str | None:
        if not item_id or not tree.exists(item_id):
            return None
        if column_id == "#0":
            return str(tree.item(item_id, "text"))
        if not column_id.startswith("#"):
            return None
        try:
            column_index = int(column_id[1:]) - 1
        except ValueError:
            return None
        values = list(tree.item(item_id, "values"))
        if not 0 <= column_index < len(values):
            return None
        return str(values[column_index])

    def _tree_selection_text(self, tree: ttk.Treeview) -> str:
        selected = list(tree.selection())
        if not selected:
            focus = tree.focus()
            selected = [focus] if focus else []
        if not selected:
            return ""

        show = str(tree.cget("show"))
        include_tree = "tree" in show
        columns = list(tree["columns"])
        headers: list[str] = []
        if include_tree:
            headers.append(str(tree.heading("#0", "text") or "名称"))
        headers.extend(str(tree.heading(column, "text") or column) for column in columns)

        rows: list[list[str]] = []
        for item_id in selected:
            row: list[str] = []
            if include_tree:
                row.append(str(tree.item(item_id, "text")))
            row.extend(str(value) for value in tree.item(item_id, "values"))
            rows.append(row)

        lines = ["\t".join(headers)]
        lines.extend("\t".join(row) for row in rows)
        return "\n".join(lines)

    def _save_export(self, table: PdmTable, content: str, ext: str, label: str) -> None:
        name = table.code or table.name or "table"
        path = filedialog.asksaveasfilename(title="导出表结构", initialfile=f"{name}{ext}", defaultextension=ext, filetypes=[(label, f"*{ext}"), ("所有文件", "*.*")])
        if not path:
            return
        Path(path).write_text(content, encoding="utf-8")
        self.set_status(f"已导出：{path}", "success")

    def close_current_tab(self, tab_key: str | None = None) -> None:
        if tab_key is None:
            selected = self.detail_tabs.select()
            for key, info in self.table_tabs.items():
                if str(info["frame"]) == selected:
                    tab_key = key
                    break
        if not tab_key:
            return
        info = self.table_tabs.pop(tab_key, None)
        if not info:
            return
        preview = info.get("preview")
        if preview in self.text_panes:
            self.text_panes.remove(preview)
        frame = info["frame"]
        self.detail_tabs.forget(frame)
        frame.destroy()
        self.set_status("表标签已关闭", "success")

    def apply_zoom(self) -> None:
        size = self.zoom.get()
        style = ttk.Style(self)
        style.configure("Treeview", rowheight=max(30, size + 20), font=ui_font(size))
        style.configure("Treeview.Heading", font=ui_font(size, "bold"))
        if hasattr(self, "home_tables"):
            self.home_tables.set_zoom(size)
        for info in self.table_tabs.values():
            fields = info.get("fields")
            if hasattr(fields, "set_zoom"):
                fields.set_zoom(size)
        for pane in self.text_panes:
            pane.text.configure(font=mono_font(size))

    def load_state(self, data: dict[str, Any]) -> None:
        self.search.set(data.get("search", ""))
        try:
            self.zoom.set(int(data.get("zoom", 10)))
        except Exception:
            self.zoom.set(10)
        paths = data.get("paths", [])
        if isinstance(paths, list) and paths:
            self.after(250, lambda: self.open_paths([str(path) for path in paths if isinstance(path, str)], restored=True))

    def get_state(self) -> dict[str, Any]:
        return {"search": self.search.get(), "zoom": self.zoom.get(), "paths": [item.path for item in self.opened_pdms]}

    def clear(self) -> None:
        for key in list(self.table_tabs):
            self.close_current_tab(key)
        self.opened_pdms = []
        self.opened_by_path = {}
        self.selected_pdm_id = None
        self.search.set("")
        self.refresh_pdm_tree()
        self.refresh_scope_options()
        self.refresh_home()
        self.pdm_status.configure(text="已清空")

    def current_table_info(self, tab_key: str | None = None) -> dict[str, Any] | None:
        if tab_key:
            return self.table_tabs.get(tab_key)
        selected = self.detail_tabs.select()
        for info in self.table_tabs.values():
            if str(info["frame"]) == selected:
                return info
        home_selection = self.home_tables.selection()
        if home_selection:
            parsed = self._parse_home_iid(home_selection[0])
            if parsed:
                pdm_id, table_index = parsed
                opened = self.get_pdm(pdm_id)
                if opened and 0 <= table_index < len(opened.model.tables):
                    return {"pdm": opened, "pdm_id": pdm_id, "table_index": table_index, "table": opened.model.tables[table_index]}
        return None

    def current_scope_id(self) -> str | None:
        index = self.home_scope_combo.current() if hasattr(self, "home_scope_combo") else 0
        if 0 <= index < len(self.home_scope_ids):
            return self.home_scope_ids[index]
        return None

    def get_pdm(self, pdm_id: str | None) -> OpenedPdm | None:
        if not pdm_id:
            return None
        for item in self.opened_pdms:
            if item.id == pdm_id:
                return item
        return None

    def table_tab_key(self, pdm_id: str, table_index: int) -> str:
        return f"{pdm_id}:{table_index}"

    def _table_iid(self, pdm_id: str, table_index: int) -> str:
        return f"table:{pdm_id}:{table_index}"

    def _home_iid(self, pdm_id: str, table_index: int) -> str:
        return f"home:{pdm_id}:{table_index}"

    def _parse_table_iid(self, item_id: str) -> tuple[str, int] | None:
        parts = item_id.split(":")
        if len(parts) != 3 or parts[0] != "table":
            return None
        try:
            return parts[1], int(parts[2])
        except ValueError:
            return None

    def _parse_home_iid(self, item_id: str) -> tuple[str, int] | None:
        parts = item_id.split(":")
        if len(parts) != 3 or parts[0] != "home":
            return None
        try:
            return parts[1], int(parts[2])
        except ValueError:
            return None

    def _table_haystack(self, table: PdmTable) -> str:
        fields = [table.code, table.name, table.comment]
        fields.extend(f"{column.code} {column.name} {column.data_type} {column.comment}" for column in table.columns)
        return " ".join(fields).lower()

    def _path_key(self, path: str) -> str:
        try:
            return str(Path(path).resolve()).casefold()
        except Exception:
            return str(Path(path)).casefold()

    def _tab_title(self, table: PdmTable) -> str:
        title = table.code or table.name or "未命名表"
        return title if len(title) <= 18 else title[:17] + "…"


class DevToolboxApp(tk.Tk):
    def __init__(self) -> None:
        enable_windows_dpi_awareness()
        super().__init__()
        configure_tk_display(self)
        self.state_store = StateStore()
        self.theme_name = self.state_store.theme
        self.title(f"{APP_TITLE}V{__version__}")
        self._set_window_icon()
        self.geometry("1320x820")
        self.minsize(1080, 680)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        apply_ttk_theme(self, self.theme_name)

        self.sidebar_items: dict[str, dict[str, Any]] = {}
        self.sidebar_icons: dict[str, dict[str, Any]] = {}
        self.sidebar_hover_key: str | None = None
        self.sidebar_hover_after_id: str | None = None
        self.pages: dict[str, ToolPage] = {}
        self.current_key = ""

        self._build_shell()
        self._create_pages()
        self.apply_theme()
        active = self.state_store.active_tool if self.state_store.active_tool in self.pages else "json"
        self.show_page(active)

    def _set_window_icon(self) -> None:
        icon_path = resource_path("assets/devtoolbox.ico")
        if not icon_path.exists():
            return
        try:
            self.iconbitmap(default=str(icon_path))
        except tk.TclError:
            pass

    def _build_shell(self) -> None:
        self.root_frame = ttk.Frame(self)
        self.root_frame.pack(fill="both", expand=True)
        self.sidebar = ttk.Frame(self.root_frame, style="Sidebar.TFrame", width=220)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = ttk.Label(self.sidebar, text="DevToolbox", style="Panel.TLabel", font=BRAND_FONT)
        brand.pack(anchor="w", padx=18, pady=(18, 2))
        version = ttk.Label(self.sidebar, text=f"WindowsEXEV{__version__}", style="PanelMuted.TLabel")
        version.pack(anchor="w", padx=18, pady=(0, 18))

        self.sidebar_menu = [
            ("json", "JSON格式化"),
            ("cron", "Cron表达式"),
            ("base64", "Base64编解码"),
            ("crypto", "加密哈希"),
            ("regex", "Regex正则表达式"),
            ("diff", "文档对比"),
            ("pdm", "PDM数据库"),
        ]
        for key, title in self.sidebar_menu:
            self._create_sidebar_item(key, title)

        filler = ttk.Frame(self.sidebar, style="Sidebar.TFrame")
        filler.pack(fill="both", expand=True)
        privacy = ttk.Label(self.sidebar, text="Author:Valiant", style="PanelMuted.TLabel", wraplength=180)
        privacy.pack(anchor="w", padx=18, pady=(0, 18))

        main = ttk.Frame(self.root_frame)
        main.pack(side="left", fill="both", expand=True)
        top = ttk.Frame(main)
        top.pack(fill="x", padx=18, pady=(16, 10))
        self.title_label = ttk.Label(top, text="", style="Title.TLabel")
        self.title_label.pack(side="left")
        ttk.Button(top, text="清空全部", command=self.clear_all).pack(side="right", padx=(8, 0))
        self.theme_button = ttk.Button(top, text="浅色主题" if self.theme_name == "dark" else "深色主题", command=self.toggle_theme)
        self.theme_button.pack(side="right", padx=(8, 0))
        ttk.Button(top, text="关于", command=self.show_about).pack(side="right")

        self.content = ttk.Frame(main)
        self.content.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        self.status = ttk.Label(main, text="就绪", style="Status.TLabel")
        self.status.pack(fill="x", padx=18, pady=(0, 12))

    def _create_pages(self) -> None:
        page_classes: list[type[ToolPage]] = [JsonPage, CronPage, Base64Page, CryptoPage, RegexPage, DocumentComparePage, PdmPage]
        for cls in page_classes:
            page = cls(self)
            self.pages[page.key] = page
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
            page.load_state(self.state_store.get_tool(page.key))

    def show_page(self, key: str) -> None:
        if key not in self.pages:
            return
        self.current_key = key
        self.state_store.active_tool = key
        page = self.pages[key]
        page.tkraise()
        self.title_label.configure(text=page.title)
        self._refresh_sidebar_styles()
        self.set_status(f"当前工具：{page.title}")

    def apply_theme(self) -> None:
        apply_ttk_theme(self, self.theme_name)
        colors = palette(self.theme_name)
        self._refresh_sidebar_icons()
        self._refresh_sidebar_styles()
        for page in self.pages.values():
            page.apply_theme(colors)
        self.theme_button.configure(text="浅色主题" if self.theme_name == "dark" else "深色主题")

    def _create_sidebar_item(self, key: str, title: str) -> None:
        colors = palette(self.theme_name)
        canvas = tk.Canvas(
            self.sidebar,
            height=44,
            bg=colors["sidebar"],
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        canvas.pack(fill="x", padx=10, pady=0)
        canvas.bind("<Button-1>", lambda _event, k=key: self.show_page(k))
        canvas.bind("<Enter>", lambda _event, k=key: self._set_sidebar_hover(k))
        canvas.bind("<Motion>", lambda _event, k=key: self._set_sidebar_hover(k))
        canvas.bind("<Leave>", lambda _event: self._schedule_sidebar_hover_check())
        canvas.bind("<Configure>", lambda _event, k=key: self._draw_sidebar_item(k))
        self.sidebar_items[key] = {
            "canvas": canvas,
            "title": title,
        }
        self._draw_sidebar_item(key)

    def _set_sidebar_hover(self, key: str) -> None:
        if self.sidebar_hover_after_id:
            self.after_cancel(self.sidebar_hover_after_id)
            self.sidebar_hover_after_id = None
        if self.sidebar_hover_key == key:
            return
        self.sidebar_hover_key = key
        self._refresh_sidebar_styles()

    def _schedule_sidebar_hover_check(self) -> None:
        if self.sidebar_hover_after_id:
            self.after_cancel(self.sidebar_hover_after_id)
        self.sidebar_hover_after_id = self.after(35, self._sync_sidebar_hover_from_pointer)

    def _sync_sidebar_hover_from_pointer(self) -> None:
        self.sidebar_hover_after_id = None
        key = self._sidebar_key_at_pointer()
        if key == self.sidebar_hover_key:
            return
        self.sidebar_hover_key = key
        self._refresh_sidebar_styles()

    def _sidebar_key_at_pointer(self) -> str | None:
        pointer_x = self.winfo_pointerx()
        pointer_y = self.winfo_pointery()
        for key, item in self.sidebar_items.items():
            canvas = item["canvas"]
            left = canvas.winfo_rootx()
            top = canvas.winfo_rooty()
            right = left + canvas.winfo_width()
            bottom = top + canvas.winfo_height()
            if left <= pointer_x < right and top <= pointer_y < bottom:
                return key
        return None

    def _refresh_sidebar_icons(self) -> None:
        colors = palette(self.theme_name)
        self.sidebar_icons = {}
        for key, _title in getattr(self, "sidebar_menu", []):
            self.sidebar_icons[key] = {
                "normal": self._make_sidebar_icon(key, colors["muted"]),
                "hover": self._make_sidebar_icon(key, colors["text"]),
                "active": self._make_sidebar_icon(key, "#ffffff"),
            }

    def _refresh_sidebar_styles(self) -> None:
        if not self.sidebar_items:
            return
        for key in self.sidebar_items:
            self._draw_sidebar_item(key)

    def _draw_sidebar_item(self, key: str) -> None:
        item = self.sidebar_items.get(key)
        if not item:
            return
        colors = palette(self.theme_name)
        active = key == self.current_key
        hover = key == self.sidebar_hover_key
        bg = colors["accent"] if active else (colors["panel_alt"] if hover else colors["sidebar"])
        fg = "#ffffff" if active else colors["text"] if hover else colors["muted"]
        state = "active" if active else "hover" if hover else "normal"

        canvas: tk.Canvas = item["canvas"]
        canvas.configure(bg=bg)
        canvas.delete("all")
        width = max(canvas.winfo_width(), 180)
        height = max(canvas.winfo_height(), 44)
        canvas.create_rectangle(0, 0, width, height, fill=bg, outline=bg)
        icon = self.sidebar_icons.get(key, {}).get(state)
        if icon is not None:
            canvas.create_image(20, height // 2, image=icon)
        canvas.create_text(52, height // 2, text=item["title"], anchor="w", fill=fg, font=UI_FONT)

    def _make_sidebar_icon(self, key: str, color: str) -> Any:
        if Image is None or ImageDraw is None or ImageTk is None:
            return None
        scale = 3
        size = 18
        canvas = size * scale
        width = 2 * scale
        image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        c = color

        def line(points: list[tuple[int, int]]) -> None:
            scaled = [(x * scale, y * scale) for x, y in points]
            draw.line(scaled, fill=c, width=width, joint="curve")

        def ellipse(box: tuple[int, int, int, int], *, outline: bool = True) -> None:
            scaled = tuple(v * scale for v in box)
            if outline:
                draw.ellipse(scaled, outline=c, width=width)
            else:
                draw.ellipse(scaled, fill=c)

        def rectangle(box: tuple[int, int, int, int], *, outline: bool = True) -> None:
            scaled = tuple(v * scale for v in box)
            if outline:
                draw.rounded_rectangle(scaled, radius=2 * scale, outline=c, width=width)
            else:
                draw.rounded_rectangle(scaled, radius=2 * scale, fill=c)

        if key == "json":
            line([(7, 3), (4, 3), (4, 8), (2, 9), (4, 10), (4, 15), (7, 15)])
            line([(11, 3), (14, 3), (14, 8), (16, 9), (14, 10), (14, 15), (11, 15)])
        elif key == "cron":
            ellipse((3, 3, 15, 15))
            line([(9, 5), (9, 9), (12, 11)])
            ellipse((8, 8, 10, 10), outline=False)
        elif key == "base64":
            rectangle((3, 4, 15, 14))
            line([(5, 11), (8, 8), (10, 10), (13, 7)])
            ellipse((5, 6, 7, 8), outline=False)
            line([(3, 15), (15, 15)])
        elif key == "crypto":
            rectangle((4, 8, 14, 15))
            line([(6, 8), (6, 6), (7, 4), (9, 3), (11, 4), (12, 6), (12, 8)])
            ellipse((8, 11, 10, 13), outline=False)
            line([(9, 12), (9, 14)])
        elif key == "regex":
            ellipse((3, 8, 5, 10), outline=False)
            line([(11, 4), (11, 14)])
            line([(7, 6), (15, 12)])
            line([(15, 6), (7, 12)])
        elif key == "diff":
            rectangle((3, 3, 11, 14))
            rectangle((7, 5, 15, 16))
            line([(5, 7), (9, 7)])
            line([(5, 10), (8, 10)])
            line([(10, 10), (13, 10)])
            line([(10, 13), (13, 13)])
        elif key == "pdm":
            draw.ellipse((4 * scale, 3 * scale, 14 * scale, 7 * scale), outline=c, width=width)
            line([(4, 5), (4, 13)])
            line([(14, 5), (14, 13)])
            draw.ellipse((4 * scale, 11 * scale, 14 * scale, 15 * scale), outline=c, width=width)
            draw.arc((4 * scale, 7 * scale, 14 * scale, 11 * scale), 0, 180, fill=c, width=width)

        image = image.resize((size, size), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(image)

    def toggle_theme(self) -> None:
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.state_store.theme = self.theme_name
        self.apply_theme()
        self.set_status("主题已切换", "success")

    def clear_all(self) -> None:
        if not messagebox.askyesno(APP_TITLE, "确认清空所有工具的当前内容？"):
            return
        for page in self.pages.values():
            page.clear()
        self.set_status("全部工具已清空", "success")

    def copy_text(self, text: str) -> None:
        if not text:
            self.set_status("没有可复制内容", "warning")
            return
        self.clipboard_clear()
        self.clipboard_append(str(text))
        self.set_status("已复制到剪贴板", "success")

    def set_status(self, text: str, kind: str = "muted") -> None:
        colors = palette(self.theme_name)
        color = colors.get(kind, colors["muted"])
        self.status.configure(text=text, foreground=color)

    def show_about(self) -> None:
        messagebox.showinfo(
            APP_TITLE,
            f"{APP_TITLE}V{__version__}\n\n"
            "本地离线开发工具箱：JSON、Cron、Base64、加密哈希、正则、文档对比、PDM查看。\n"
            "所有数据仅在本机处理，不联网、不上传、不写注册表。\n\n"
            "Author:Valiant",
        )

    def on_close(self) -> None:
        for key, page in self.pages.items():
            self.state_store.set_tool(key, page.get_state())
        self.state_store.theme = self.theme_name
        self.state_store.active_tool = self.current_key or "json"
        self.state_store.save()
        self.destroy()


def main() -> None:
    app = DevToolboxApp()
    app.mainloop()
