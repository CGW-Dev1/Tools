from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


APP_DIR_NAME = "DevToolbox"
STATE_FILE = "state.json"


def config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".dev_toolbox"


class StateStore:
    def __init__(self) -> None:
        self.path = config_dir() / STATE_FILE
        self.data: dict[str, Any] = {
            "theme": "dark",
            "active_tool": "json",
            "tools": {},
        }
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
                    self.data.setdefault("tools", {})
        except Exception:
            self.data = {
                "theme": "dark",
                "active_tool": "json",
                "tools": {},
            }

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def get_tool(self, key: str) -> dict[str, Any]:
        tools = self.data.setdefault("tools", {})
        value = tools.get(key, {})
        return value if isinstance(value, dict) else {}

    def set_tool(self, key: str, value: dict[str, Any]) -> None:
        self.data.setdefault("tools", {})[key] = value

    @property
    def theme(self) -> str:
        theme = self.data.get("theme", "dark")
        return theme if theme in {"dark", "light"} else "dark"

    @theme.setter
    def theme(self, value: str) -> None:
        self.data["theme"] = value if value in {"dark", "light"} else "dark"

    @property
    def active_tool(self) -> str:
        value = self.data.get("active_tool", "json")
        return value if isinstance(value, str) else "json"

    @active_tool.setter
    def active_tool(self, value: str) -> None:
        self.data["active_tool"] = value
