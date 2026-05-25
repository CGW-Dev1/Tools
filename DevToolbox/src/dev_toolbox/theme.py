from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .display import SECTION_FONT, TITLE_FONT, UI_FONT


THEMES = {
    "dark": {
        "bg": "#11151c",
        "sidebar": "#151b24",
        "panel": "#1b2330",
        "panel_alt": "#202a38",
        "field": "#0f141b",
        "text": "#f2f6fb",
        "muted": "#b0bdcc",
        "border": "#303b4c",
        "accent": "#4c8dff",
        "accent_hover": "#6aa2ff",
        "selection": "#28476f",
        "success": "#65d38a",
        "warning": "#f2c36b",
        "error": "#ff6b6b",
        "json_key": "#8ed1ff",
        "json_string": "#9fe870",
        "json_number": "#f2c36b",
        "json_bool": "#ff9f7a",
        "match": "#674ea7",
        "diff_insert": "#143523",
        "diff_delete": "#402028",
        "diff_change": "#3d321c",
        "diff_header": "#25344a",
    },
    "light": {
        "bg": "#f4f6fa",
        "sidebar": "#eef2f7",
        "panel": "#ffffff",
        "panel_alt": "#f7f9fc",
        "field": "#ffffff",
        "text": "#1f2937",
        "muted": "#657386",
        "border": "#d7dee9",
        "accent": "#2563eb",
        "accent_hover": "#1d4ed8",
        "selection": "#cfe0ff",
        "success": "#15803d",
        "warning": "#a16207",
        "error": "#dc2626",
        "json_key": "#005cc5",
        "json_string": "#22863a",
        "json_number": "#b08800",
        "json_bool": "#d73a49",
        "match": "#ffe082",
        "diff_insert": "#dcfce7",
        "diff_delete": "#fee2e2",
        "diff_change": "#fef3c7",
        "diff_header": "#dbeafe",
    },
}


def palette(name: str) -> dict[str, str]:
    return THEMES.get(name, THEMES["dark"])


def apply_ttk_theme(root: tk.Tk, name: str) -> None:
    colors = palette(name)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=colors["bg"])
    style.configure(".", font=UI_FONT)
    style.configure("TFrame", background=colors["bg"])
    style.configure("Panel.TFrame", background=colors["panel"])
    style.configure("Alt.TFrame", background=colors["panel_alt"])
    style.configure("Sidebar.TFrame", background=colors["sidebar"])
    style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
    style.configure("Panel.TLabel", background=colors["panel"], foreground=colors["text"])
    style.configure("Muted.TLabel", background=colors["bg"], foreground=colors["muted"])
    style.configure("PanelMuted.TLabel", background=colors["panel"], foreground=colors["muted"])
    style.configure("Title.TLabel", font=TITLE_FONT, foreground=colors["text"], background=colors["bg"])
    style.configure("Section.TLabel", font=SECTION_FONT, foreground=colors["text"], background=colors["bg"])
    style.configure("Status.TLabel", foreground=colors["muted"], background=colors["bg"])

    style.configure(
        "TButton",
        background=colors["panel_alt"],
        foreground=colors["text"],
        bordercolor=colors["border"],
        focusthickness=1,
        focuscolor=colors["accent"],
        padding=(12, 7),
        font=UI_FONT,
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("active", colors["accent"]), ("pressed", colors["accent_hover"])],
        foreground=[("active", "#ffffff"), ("pressed", "#ffffff")],
    )
    style.configure(
        "Accent.TButton",
        background=colors["accent"],
        foreground="#ffffff",
        bordercolor=colors["accent"],
    )
    style.map("Accent.TButton", background=[("active", colors["accent_hover"])])
    style.configure(
        "Sidebar.TButton",
        background=colors["sidebar"],
        foreground=colors["muted"],
        anchor="w",
        padding=(16, 11),
        font=UI_FONT,
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Sidebar.TButton",
        background=[("active", colors["panel_alt"])],
        foreground=[("active", colors["text"])],
    )
    style.configure(
        "ActiveSidebar.TButton",
        background=colors["accent"],
        foreground="#ffffff",
        anchor="w",
        padding=(16, 11),
        font=UI_FONT,
        relief="flat",
        borderwidth=0,
    )
    style.map("ActiveSidebar.TButton", background=[("active", colors["accent_hover"])])

    style.configure(
        "TEntry",
        fieldbackground=colors["field"],
        foreground=colors["text"],
        insertcolor=colors["text"],
        bordercolor=colors["border"],
        lightcolor=colors["border"],
        darkcolor=colors["border"],
        padding=(7, 6),
        font=UI_FONT,
    )
    style.configure(
        "TCombobox",
        fieldbackground=colors["field"],
        background=colors["field"],
        foreground=colors["text"],
        arrowcolor=colors["text"],
        bordercolor=colors["border"],
        padding=(7, 6),
        font=UI_FONT,
    )
    style.map("TCombobox", fieldbackground=[("readonly", colors["field"])])
    style.configure("TCheckbutton", background=colors["bg"], foreground=colors["text"])
    style.map("TCheckbutton", background=[("active", colors["bg"])])
    style.configure("TRadiobutton", background=colors["bg"], foreground=colors["text"])
    style.map("TRadiobutton", background=[("active", colors["bg"])])
    style.configure("TLabelframe", background=colors["bg"], foreground=colors["text"], bordercolor=colors["border"])
    style.configure("TLabelframe.Label", background=colors["bg"], foreground=colors["muted"])

    style.configure(
        "TNotebook",
        background=colors["bg"],
        borderwidth=0,
        tabmargins=(0, 0, 0, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=colors["panel"],
        foreground=colors["muted"],
        padding=(14, 8),
        font=UI_FONT,
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", colors["panel_alt"]), ("active", colors["panel_alt"])],
        foreground=[("selected", colors["text"]), ("active", colors["text"])],
    )
    style.configure(
        "Treeview",
        background=colors["field"],
        fieldbackground=colors["field"],
        foreground=colors["text"],
        bordercolor=colors["border"],
        rowheight=28,
        font=UI_FONT,
    )
    style.configure(
        "Treeview.Heading",
        background=colors["panel_alt"],
        foreground=colors["text"],
        relief="flat",
        padding=(6, 6),
        font=SECTION_FONT,
    )
    style.map("Treeview", background=[("selected", colors["selection"])], foreground=[("selected", colors["text"])])
