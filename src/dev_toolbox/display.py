from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import font as tkfont


UI_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"
UI_FONT = "DevToolboxUIFont"
UI_BOLD_FONT = "DevToolboxUIBoldFont"
TITLE_FONT = "DevToolboxTitleFont"
SECTION_FONT = "DevToolboxSectionFont"
SIDEBAR_FONT = "DevToolboxSidebarFont"
BRAND_FONT = "DevToolboxBrandFont"
MONO_FONT = "DevToolboxMonoFont"


def enable_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        awareness_context = ctypes.c_void_p(-4)  # PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(awareness_context):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def configure_tk_display(root: tk.Tk) -> None:
    try:
        dpi = root.winfo_fpixels("1i")
        root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
    except Exception:
        pass
    _configure_named_fonts(root)


def ui_font(size: int = 10, weight: str = "normal") -> tuple[str, int, str]:
    return (UI_FAMILY, size, weight)


def mono_font(size: int = 11, weight: str = "normal") -> tuple[str, int, str]:
    return (MONO_FAMILY, size, weight)


def _configure_named_fonts(root: tk.Tk) -> None:
    fonts = {
        "TkDefaultFont": ui_font(10),
        "TkTextFont": mono_font(11),
        "TkFixedFont": mono_font(11),
        "TkMenuFont": ui_font(10),
        "TkHeadingFont": ui_font(10, "bold"),
        "TkCaptionFont": ui_font(10),
        "TkSmallCaptionFont": ui_font(9),
        "TkIconFont": ui_font(10),
        "TkTooltipFont": ui_font(9),
    }
    for name, value in fonts.items():
        try:
            tkfont.nametofont(name).configure(family=value[0], size=value[1], weight=value[2])
        except tk.TclError:
            pass

    _ensure_font(root, UI_FONT, ui_font(10))
    _ensure_font(root, UI_BOLD_FONT, ui_font(10, "bold"))
    _ensure_font(root, TITLE_FONT, ui_font(17, "bold"))
    _ensure_font(root, SECTION_FONT, ui_font(11, "bold"))
    _ensure_font(root, SIDEBAR_FONT, ui_font(10))
    _ensure_font(root, BRAND_FONT, ui_font(16, "bold"))
    _ensure_font(root, MONO_FONT, mono_font(11))
    root.option_add("*Font", UI_FONT)


def _ensure_font(root: tk.Tk, name: str, value: tuple[str, int, str]) -> None:
    try:
        font = tkfont.nametofont(name)
        font.configure(family=value[0], size=value[1], weight=value[2])
    except tk.TclError:
        tkfont.Font(root=root, name=name, family=value[0], size=value[1], weight=value[2])
