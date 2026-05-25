from __future__ import annotations

import base64
import csv
import ctypes
import ctypes.wintypes
import email
import imaplib
import json
import queue
import re
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
import tkinter as tk
import tkinter.font as tkfont
from urllib.parse import urlencode

import msal
import requests


APP_NAME = "OutlookHotmailMailFetcher"
DISPLAY_NAME = "邮件验证码助手"
AUTHORITY_BASE = "https://login.microsoftonline.com"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_INTERACTIVE_SCOPES = ["Mail.Read", "offline_access"]
GRAPH_REFRESH_SCOPE_OPTIONS: list[str | None] = [
    "https://graph.microsoft.com/Mail.Read offline_access",
    "Mail.Read offline_access",
    "https://graph.microsoft.com/.default",
    None,
]
IMAP_REFRESH_SCOPE_OPTIONS: list[str | None] = [
    "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
    "IMAP.AccessAsUser.All offline_access",
]
IMAP_HOST = "outlook.office365.com"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_PATTERNS = [
    re.compile(r"(?i)(?:验证码|校验码|动态码|安全代码|verification code|security code|code|otp|pin)[^A-Z0-9]{0,24}([A-Z0-9]{4,10})"),
    re.compile(r"(?<!\d)(\d{4,8})(?!\d)"),
]

BG = "#eaf7ff"
PANEL = "#f6fbff"
CARD = "#f8fbff"
BORDER = "#d9e8fb"
TEXT = "#223047"
MUTED = "#60758f"
BLUE = "#2f6fed"
BLUE_DARK = "#0b9bd8"
GREEN = "#12b981"
GREEN_BG = "#dcf8f0"
RED = "#ef4444"


def enable_dpi_awareness() -> None:
    if not hasattr(ctypes, "windll"):
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def configure_default_fonts() -> None:
    for name, size in (
        ("TkDefaultFont", 10),
        ("TkTextFont", 10),
        ("TkMenuFont", 10),
        ("TkHeadingFont", 10),
        ("TkTooltipFont", 9),
        ("TkCaptionFont", 10),
        ("TkSmallCaptionFont", 9),
        ("TkIconFont", 10),
    ):
        try:
            tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=size)
        except tk.TclError:
            continue


def app_data_dir() -> Path:
    path = Path.home() / "AppData" / "Roaming" / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


class WindowsDpapi:
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    @classmethod
    def _blob_from_bytes(cls, data: bytes) -> "WindowsDpapi.DATA_BLOB":
        buf = ctypes.create_string_buffer(data)
        blob = cls.DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
        blob._buffer = buf
        return blob

    @classmethod
    def protect(cls, data: bytes) -> bytes:
        if not data:
            return b""
        in_blob = cls._blob_from_bytes(data)
        out_blob = cls.DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob)
        )
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)

    @classmethod
    def unprotect(cls, data: bytes) -> bytes:
        if not data:
            return b""
        in_blob = cls._blob_from_bytes(data)
        out_blob = cls.DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob)
        )
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)


class EncryptedTextFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read_text(self) -> str:
        if not self.path.exists():
            return ""
        raw = self.path.read_bytes()
        if not raw:
            return ""
        return WindowsDpapi.unprotect(base64.b64decode(raw)).decode("utf-8")

    def write_text(self, text: str) -> None:
        encrypted = WindowsDpapi.protect(text.encode("utf-8"))
        self.path.write_bytes(base64.b64encode(encrypted))


@dataclass
class AccountRecord:
    email: str
    password: str = ""
    client_id: str = ""
    refresh_token: str = ""
    imported_at: str = ""
    last_fetch_at: str = ""
    last_status: str = "未取件"
    used: bool = False

    @property
    def source(self) -> str:
        return "OAuth令牌" if self.client_id and self.refresh_token else "交互授权"


@dataclass
class ImportRecord:
    email: str
    password: str = ""
    client_id: str = ""
    refresh_token: str = ""


class AccountStore:
    def __init__(self) -> None:
        self.legacy_path = app_data_dir() / "accounts.json"
        self.path = app_data_dir() / "accounts.sec"
        self.secure_file = EncryptedTextFile(self.path)
        self.lock = threading.RLock()
        self.accounts: list[AccountRecord] = []
        self.load()

    def load(self) -> None:
        try:
            text = ""
            if self.path.exists():
                text = self.secure_file.read_text()
            elif self.legacy_path.exists():
                text = self.legacy_path.read_text(encoding="utf-8")
            if not text:
                self.accounts = []
                return
            data = json.loads(text)
            self.accounts = [AccountRecord(**self._normalize(item)) for item in data.get("accounts", [])]
        except Exception:
            self.accounts = []
            return
        try:
            self.save()
        except Exception:
            pass

    def _normalize(self, item: dict) -> dict:
        return {
            "email": item.get("email", ""),
            "password": item.get("password", ""),
            "client_id": item.get("client_id", ""),
            "refresh_token": item.get("refresh_token", ""),
            "imported_at": item.get("imported_at") or datetime.now(timezone.utc).isoformat(),
            "last_fetch_at": item.get("last_fetch_at", ""),
            "last_status": item.get("last_status", "未取件"),
            "used": bool(item.get("used", False)),
        }

    def save(self) -> None:
        with self.lock:
            data = {"accounts": [asdict(account) for account in self.accounts]}
            self.secure_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def upsert_records(self, records: list[ImportRecord]) -> tuple[int, int, int]:
        with self.lock:
            existing = {account.email.lower(): account for account in self.accounts}
            added = updated = skipped = 0
            now = datetime.now(timezone.utc).isoformat()
            for record in records:
                current = existing.get(record.email.lower())
                if current:
                    skipped += 1
                    continue
                account = AccountRecord(
                    email=record.email,
                    password=record.password,
                    client_id=record.client_id,
                    refresh_token=record.refresh_token,
                    imported_at=now,
                    last_status="已导入" if record.refresh_token else "未取件",
                )
                self.accounts.append(account)
                existing[account.email.lower()] = account
                added += 1
            self.save()
            return added, updated, skipped

    def get(self, email_address: str) -> AccountRecord | None:
        with self.lock:
            for account in self.accounts:
                if account.email.lower() == email_address.lower():
                    return account
        return None

    def mark(self, email_address: str, status: str, fetched: bool = False) -> None:
        with self.lock:
            account = self.get(email_address)
            if not account:
                return
            account.last_status = status
            if fetched:
                account.last_fetch_at = datetime.now(timezone.utc).isoformat()
            self.save()

    def update_refresh_token(self, email_address: str, refresh_token: str) -> None:
        with self.lock:
            account = self.get(email_address)
            if account and refresh_token and account.refresh_token != refresh_token:
                account.refresh_token = refresh_token
                self.save()

    def set_used(self, emails: set[str], used: bool) -> int:
        with self.lock:
            changed = 0
            for account in self.accounts:
                if account.email in emails and account.used != used:
                    account.used = used
                    changed += 1
            if changed:
                self.save()
            return changed

    def remove(self, emails: set[str]) -> int:
        with self.lock:
            before = len(self.accounts)
            self.accounts = [account for account in self.accounts if account.email not in emails]
            self.save()
            return before - len(self.accounts)

    def clear(self) -> int:
        with self.lock:
            total = len(self.accounts)
            self.accounts = []
            self.save()
            return total


class ConfigStore:
    def __init__(self) -> None:
        self.path = app_data_dir() / "config.json"
        self.client_id = ""
        self.tenant = "consumers"
        self.top = 10
        self.protocol = "Graph"
        self.auto_fetch_after_import = True
        self.concise_mode = False
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.client_id = data.get("client_id", "")
            self.tenant = data.get("tenant", "consumers")
            self.top = max(1, min(int(data.get("top", 10)), 50))
            self.protocol = "Graph"
            self.auto_fetch_after_import = bool(data.get("auto_fetch_after_import", True))
            self.concise_mode = bool(data.get("concise_mode", False))
        except Exception:
            self.protocol = "Graph"

    def save(self) -> None:
        data = {
            "client_id": self.client_id,
            "tenant": self.tenant,
            "top": self.top,
            "protocol": self.protocol,
            "auto_fetch_after_import": self.auto_fetch_after_import,
            "concise_mode": self.concise_mode,
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class DirectOAuthClient:
    def __init__(self, config: ConfigStore, account_store: AccountStore) -> None:
        self.config = config
        self.account_store = account_store

    @property
    def token_url(self) -> str:
        tenant = self.config.tenant or "consumers"
        return f"{AUTHORITY_BASE}/{tenant}/oauth2/v2.0/token"

    def refresh_access_token(self, account: AccountRecord, scope_options: list[str | None]) -> str:
        if not account.client_id or not account.refresh_token:
            raise RuntimeError("缺少 client_id 或 refresh_token")
        errors: list[str] = []
        for scope in scope_options:
            data = {
                "client_id": account.client_id,
                "grant_type": "refresh_token",
                "refresh_token": account.refresh_token,
            }
            if scope:
                data["scope"] = scope
            try:
                response = requests.post(self.token_url, data=data, timeout=30)
                payload = response.json() if response.content else {}
            except Exception as exc:
                errors.append(str(exc))
                continue
            if response.status_code < 400 and payload.get("access_token"):
                if payload.get("refresh_token"):
                    self.account_store.update_refresh_token(account.email, payload["refresh_token"])
                return payload["access_token"]
            errors.append(payload.get("error_description") or payload.get("error") or response.text[:300])
        raise RuntimeError("刷新 Graph 访问令牌失败：" + " | ".join(errors[-2:]))


class GraphMailClient:
    def __init__(self, config: ConfigStore, account_store: AccountStore) -> None:
        self.config = config
        self.direct = DirectOAuthClient(config, account_store)
        self.cache_file = EncryptedTextFile(app_data_dir() / "msal_cache.dat")
        self.cache = msal.SerializableTokenCache()
        cached = self.cache_file.read_text()
        if cached:
            self.cache.deserialize(cached)
        self.app = self._build_app() if config.client_id else None

    def _build_app(self) -> msal.PublicClientApplication:
        return msal.PublicClientApplication(
            client_id=self.config.client_id,
            authority=f"{AUTHORITY_BASE}/{self.config.tenant}",
            token_cache=self.cache,
        )

    def save_cache(self) -> None:
        if self.cache.has_state_changed:
            self.cache_file.write_text(self.cache.serialize())

    def authorize(self, email_address: str) -> dict:
        if not self.app:
            raise RuntimeError("交互授权需要先填写全局 Client ID")
        result = self.app.acquire_token_interactive(scopes=GRAPH_INTERACTIVE_SCOPES, login_hint=email_address)
        self.save_cache()
        return result

    def access_token(self, account: AccountRecord) -> str:
        if account.client_id and account.refresh_token:
            return self.direct.refresh_access_token(account, GRAPH_REFRESH_SCOPE_OPTIONS)
        if not self.app:
            raise RuntimeError("没有 refresh_token，也没有全局 Client ID 授权缓存")
        accounts = self.app.get_accounts(username=account.email)
        if not accounts:
            raise RuntimeError("未找到授权缓存")
        result = self.app.acquire_token_silent(scopes=GRAPH_INTERACTIVE_SCOPES, account=accounts[0])
        self.save_cache()
        if not result or "access_token" not in result:
            raise RuntimeError("授权缓存失效")
        return result["access_token"]

    def latest_messages(self, account: AccountRecord, top: int) -> list[dict]:
        token = self.access_token(account)
        query = urlencode(
            {
                "$top": max(1, min(top, 50)),
                "$orderby": "receivedDateTime desc",
                "$select": "receivedDateTime,from,sender,subject,bodyPreview,webLink,isRead",
            }
        )
        url = f"{GRAPH_BASE}/me/mailFolders/inbox/messages?{query}"
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Prefer": 'outlook.body-content-type="text"',
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Graph 请求失败 HTTP {response.status_code}: {response.text[:500]}")
        return response.json().get("value", [])


class ImapMailClient:
    def __init__(self, config: ConfigStore, account_store: AccountStore) -> None:
        self.direct = DirectOAuthClient(config, account_store)

    def latest_messages(self, account: AccountRecord, top: int) -> list[dict]:
        token = self.direct.refresh_access_token(account, IMAP_REFRESH_SCOPE_OPTIONS)
        auth = f"user={account.email}\x01auth=Bearer {token}\x01\x01"
        with imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=30) as client:
            client.authenticate("XOAUTH2", lambda _challenge: auth.encode("utf-8"))
            client.select("INBOX", readonly=True)
            status, data = client.search(None, "ALL")
            if status != "OK" or not data or not data[0]:
                return []
            ids = data[0].split()[-max(1, min(top, 50)) :]
            rows: list[dict] = []
            for msg_id in reversed(ids):
                status, fetched = client.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue
                raw = next((part[1] for part in fetched if isinstance(part, tuple)), b"")
                if raw:
                    rows.append(parse_imap_message(raw, account.email))
            return rows


def parse_import_text(text: str) -> tuple[list[ImportRecord], int]:
    records: list[ImportRecord] = []
    invalid = 0
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip().strip("\ufeff").rstrip(",;")
        if not line:
            continue
        parts = [part.strip() for part in line.split("----")]
        if not parts or not EMAIL_RE.match(parts[0]):
            invalid += 1
            continue
        key = parts[0].lower()
        if key in seen:
            continue
        seen.add(key)
        records.append(
            ImportRecord(
                email=parts[0],
                password=parts[1] if len(parts) > 1 else "",
                client_id=parts[2] if len(parts) > 2 else "",
                refresh_token=parts[3] if len(parts) > 3 else "",
            )
        )
    return records, invalid


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            parts.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts).strip()


def extract_preview(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() == "text/plain":
                raw = part.get_payload(decode=True) or b""
                return raw.decode(part.get_content_charset() or "utf-8", errors="replace").strip()[:800]
    if msg.get_content_type() == "text/plain":
        raw = msg.get_payload(decode=True) or b""
        return raw.decode(msg.get_content_charset() or "utf-8", errors="replace").strip()[:800]
    return ""


def parse_imap_message(raw: bytes, account: str) -> dict:
    msg = email.message_from_bytes(raw)
    return {
        "account": account,
        "protocol": "IMAP",
        "time": fmt_dt(msg.get("Date") or ""),
        "sender": decode_mime_header(msg.get("From")),
        "subject": decode_mime_header(msg.get("Subject")),
        "read": "",
        "preview": extract_preview(msg),
        "webLink": "",
    }


def fmt_dt(value: str) -> str:
    if not value:
        return ""
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone().strftime("%m/%d %H:%M")
    except ValueError:
        try:
            return parsedate_to_datetime(value).astimezone().strftime("%m/%d %H:%M")
        except Exception:
            return value


class ScrollFrame(tk.Frame):
    def __init__(self, master, bg: str):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda _e: self.update_scrollregion())
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.bind_mousewheel_recursive(self)

    def update_scrollregion(self) -> None:
        self.update_idletasks()
        bbox = self.canvas.bbox("all")
        if not bbox:
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            return
        view_height = max(self.canvas.winfo_height(), 1)
        content_height = max(bbox[3] - bbox[1], view_height)
        self.canvas.configure(scrollregion=(0, 0, bbox[2], content_height))
        top, bottom = self.canvas.yview()
        if bottom >= 1 and content_height <= view_height:
            self.canvas.yview_moveto(0)

    def _on_mousewheel(self, event) -> None:
        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")

    def bind_mousewheel_recursive(self, widget) -> None:
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_mousewheel, add="+")
        widget.bind("<Button-5>", self._on_mousewheel, add="+")
        for child in widget.winfo_children():
            self.bind_mousewheel_recursive(child)


class RedCheck(tk.Canvas):
    def __init__(self, master, variable: tk.BooleanVar, text: str = "", command=None, bg: str = PANEL, fg: str = MUTED):
        super().__init__(master, width=120 if text else 26, height=26, bg=bg, highlightthickness=0, cursor="hand2")
        self.variable = variable
        self.text = text
        self.command = command
        self.bg = bg
        self.fg = fg
        self.bind("<Button-1>", self.toggle)
        self.variable.trace_add("write", lambda *_args: self.draw())
        self.draw()

    def toggle(self, _event=None) -> None:
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()

    def draw(self) -> None:
        self.delete("all")
        self.create_rectangle(2, 2, 20, 20, fill="white", outline="#8fb8ff", width=2)
        if self.variable.get():
            self.create_text(11, 10, text="✓", fill=RED, font=("Segoe UI Symbol", 15, "bold"))
        if self.text:
            self.create_text(30, 11, text=self.text, anchor="w", fill=self.fg, font=("Microsoft YaHei UI", 9))


class PlaceholderEntry(tk.Entry):
    def __init__(self, master, variable: tk.StringVar, placeholder: str, command=None, **kwargs):
        super().__init__(master, **kwargs)
        self.variable = variable
        self.placeholder = placeholder
        self.command = command
        self.normal_fg = kwargs.get("fg", TEXT)
        self.placeholder_fg = "#94a9c3"
        self.placeholder_visible = False
        self.bind("<FocusIn>", self._clear_placeholder, add="+")
        self.bind("<FocusOut>", self._show_placeholder_if_empty, add="+")
        self.bind("<KeyRelease>", self._sync_variable, add="+")
        value = self.variable.get().strip()
        if value:
            self.insert(0, value)
        else:
            self._show_placeholder()

    def _show_placeholder(self) -> None:
        self.placeholder_visible = True
        self.configure(fg=self.placeholder_fg)
        self.delete(0, tk.END)
        self.insert(0, self.placeholder)

    def _clear_placeholder(self, _event=None) -> None:
        if not self.placeholder_visible:
            return
        self.placeholder_visible = False
        self.configure(fg=self.normal_fg)
        self.delete(0, tk.END)

    def _show_placeholder_if_empty(self, _event=None) -> None:
        if not self.get().strip():
            self.variable.set("")
            self._show_placeholder()

    def _sync_variable(self, _event=None) -> None:
        if self.placeholder_visible:
            self.variable.set("")
        else:
            self.variable.set(self.get())
        if self.command:
            self.command()


class ImportDialog(tk.Toplevel):
    def __init__(self, master: "MailFetcherApp") -> None:
        super().__init__(master)
        self.master_app = master
        self.title("批量导入邮箱")
        self.geometry("820x620")
        self.minsize(760, 560)
        self.configure(bg=BG)
        self.grab_set()

        box = tk.Frame(self, bg=PANEL, padx=18, pady=18)
        box.pack(fill="both", expand=True, padx=18, pady=18)
        tk.Label(box, text="批量导入邮箱", bg=PANEL, fg=TEXT, font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        tk.Label(
            box,
            text="每行格式：email----password----client_id----graph_refresh_token。四段都会加密保存在本机。",
            bg=PANEL,
            fg=MUTED,
            font=("Microsoft YaHei UI", 10),
            anchor="w",
            justify="left",
            wraplength=740,
        ).pack(fill="x", anchor="w", pady=(4, 10))
        bar = tk.Frame(box, bg=PANEL)
        bar.pack(side="bottom", fill="x", pady=(12, 0))
        make_button(bar, "从文件载入", self.load_file, bg="#eef6ff", fg=TEXT).pack(side="left")
        make_button(bar, "取消", self.destroy, bg="#eef6ff", fg=TEXT).pack(side="right")
        make_button(bar, "导入并取件", self.import_now, bg=BLUE, fg="white").pack(side="right", padx=(0, 8))
        text_box = tk.Frame(box, bg="white", highlightbackground="#9fc2ff", highlightthickness=1)
        text_box.pack(fill="both", expand=True)
        self.text = ScrolledText(text_box, height=14, wrap="none", relief="flat", bd=0, font=("Consolas", 10))
        self.text.pack(fill="both", expand=True, padx=1, pady=1)

    def load_file(self) -> None:
        path = filedialog.askopenfilename(title="选择账号文本", filetypes=[("Text files", "*.txt *.csv"), ("All files", "*.*")])
        if not path:
            return
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", Path(path).read_text(encoding="utf-8", errors="ignore"))

    def import_now(self) -> None:
        records, invalid = parse_import_text(self.text.get("1.0", tk.END))
        if not records:
            messagebox.showwarning("没有账号", "没有识别到有效邮箱。")
            return
        added, updated, skipped = self.master_app.account_store.upsert_records(records)
        self.master_app.set_account_group("unused")
        self.master_app.log(f"导入完成：新增 {added}，更新 {updated}，重复 {skipped}。四段内容已加密保存。")
        if invalid:
            self.master_app.log(f"跳过 {invalid} 行无效邮箱。")
        emails = [record.email for record in records]
        self.destroy()
        if self.master_app.auto_fetch_var.get():
            self.master_app.fetch_accounts(emails)


class DetailDialog(tk.Toplevel):
    def __init__(self, master: "MailFetcherApp", row: dict) -> None:
        super().__init__(master)
        self.title("邮件详情")
        self.geometry("840x540")
        self.configure(bg=BG)
        box = tk.Frame(self, bg=PANEL, padx=18, pady=18)
        box.pack(fill="both", expand=True, padx=18, pady=18)
        title = row.get("subject") or "(无主题)"
        tk.Label(box, text=title, bg=PANEL, fg=TEXT, font=("Microsoft YaHei UI", 15, "bold"), wraplength=760, justify="left").pack(anchor="w")
        meta = f"{row.get('sender', '')}    {row.get('time', '')}    {row.get('protocol', '')}    {row.get('account', '')}"
        tk.Label(box, text=meta, bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(5, 12))
        text = ScrolledText(box, wrap="word", height=20, font=("Microsoft YaHei UI", 10))
        text.pack(fill="both", expand=True)
        text.insert("1.0", row.get("preview") or "")
        text.configure(state="disabled")
        bar = tk.Frame(box, bg=PANEL)
        bar.pack(fill="x", pady=(12, 0))
        if row.get("webLink"):
            make_button(bar, "打开网页版", lambda: webbrowser.open(row["webLink"]), bg=BLUE, fg="white").pack(side="left")
        make_button(bar, "关闭", self.destroy, bg="#eef6ff", fg=TEXT).pack(side="right")


def rounded_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs) -> int:
    radius = min(radius, max(0, (x2 - x1) // 2), max(0, (y2 - y1) // 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class RoundButton(tk.Canvas):
    def __init__(self, master, text: str, command, bg: str = BLUE, fg: str = "white", width: int | None = None):
        pixel_width = width * 12 + 38 if width else max(112, len(text) * 13 + 34)
        super().__init__(master, width=pixel_width, height=42, bg=master.cget("bg"), highlightthickness=0, cursor="hand2")
        self.text = text
        self.command = command
        self.button_bg = bg
        self.fg = fg
        self.radius = 10
        self.bind("<Button-1>", lambda _e: self.command())
        self.bind("<Enter>", lambda _e: self.draw(hover=True))
        self.bind("<Leave>", lambda _e: self.draw(hover=False))
        self.bind("<Configure>", lambda _e: self.draw())
        self.draw()

    def configure(self, cnf=None, **kwargs):
        if "text" in kwargs:
            self.text = kwargs.pop("text")
        if "bg" in kwargs:
            self.button_bg = kwargs.pop("bg")
        if "fg" in kwargs:
            self.fg = kwargs.pop("fg")
        result = super().configure(cnf or {}, **kwargs)
        self.draw()
        return result

    config = configure

    def draw(self, hover: bool = False) -> None:
        self.delete("all")
        width = max(self.winfo_width(), int(self.cget("width")))
        height = max(self.winfo_height(), int(self.cget("height")))
        fill = adjust_color(self.button_bg, 0.96 if hover else 1.0)
        rounded_rect(self, 1, 1, width - 1, height - 1, self.radius, fill=fill, outline="")
        self.create_text(width // 2, height // 2, text=self.text, fill=self.fg, font=("Microsoft YaHei UI", 10, "bold"))


class RoundBadge(tk.Canvas):
    def __init__(self, master, textvariable: tk.StringVar, bg: str, fg: str):
        super().__init__(master, width=96, height=30, bg=master.cget("bg"), highlightthickness=0)
        self.textvariable = textvariable
        self.badge_bg = bg
        self.fg = fg
        self.textvariable.trace_add("write", lambda *_args: self.draw())
        self.bind("<Configure>", lambda _e: self.draw())
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        text = self.textvariable.get()
        width = max(80, len(text) * 9 + 28, self.winfo_width())
        height = max(26, self.winfo_height())
        self.configure(width=width)
        rounded_rect(self, 1, 1, width - 1, height - 1, 10, fill=self.badge_bg, outline="")
        self.create_text(width // 2, height // 2, text=text, fill=self.fg, font=("Microsoft YaHei UI", 9, "bold"))


def adjust_color(color: str, factor: float) -> str:
    if not color.startswith("#") or len(color) != 7:
        return color
    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    rgb = [max(0, min(255, int(value * factor))) for value in rgb]
    return "#" + "".join(f"{value:02x}" for value in rgb)


def make_button(master, text: str, command, bg: str = BLUE, fg: str = "white", width: int | None = None) -> tk.Button:
    return RoundButton(master, text=text, command=command, bg=bg, fg=fg, width=width)


def make_copy_button(master, command) -> tk.Button:
    return tk.Button(
        master,
        text="复制",
        command=command,
        bg="white",
        fg=BLUE,
        activebackground="#edf5ff",
        activeforeground=BLUE,
        relief="flat",
        bd=0,
        width=4,
        padx=4,
        pady=5,
        cursor="hand2",
        font=("Microsoft YaHei UI", 9, "bold"),
    )


def make_search_box(master, variable: tk.StringVar, placeholder: str, command=None) -> tk.Frame:
    box = tk.Frame(master, bg="white", padx=10, pady=2, highlightbackground="#8fb8ff", highlightthickness=2)
    entry = PlaceholderEntry(
        box,
        variable,
        placeholder,
        relief="flat",
        bd=0,
        bg="white",
        fg=TEXT,
        insertbackground=BLUE,
        selectbackground="#dbeafe",
        font=("Microsoft YaHei UI", 10),
        command=command,
    )
    entry.pack(fill="both", expand=True, ipady=7)
    return box


class MailFetcherApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        configure_default_fonts()
        self.title(f"{DISPLAY_NAME} | IMAP + Graph API")
        self.geometry("1480x860")
        self.minsize(1180, 720)
        self.configure(bg=BG)

        self.config_store = ConfigStore()
        self.account_store = AccountStore()
        self.graph: GraphMailClient | None = None
        self.imap = ImapMailClient(self.config_store, self.account_store)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stop_requested = threading.Event()
        self.account_vars: dict[str, tk.BooleanVar] = {}
        self.mail_rows: list[dict] = []
        self.logs: list[str] = []
        self.render_pending = False
        self.graph_count = 0
        self.imap_count = 0
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="")

        self.client_id_var = tk.StringVar(value=self.config_store.client_id)
        self.tenant_var = tk.StringVar(value=self.config_store.tenant)
        self.protocol_var = tk.StringVar(value=self.config_store.protocol)
        self.top_var = tk.StringVar(value=str(self.config_store.top))
        self.auto_fetch_var = tk.BooleanVar(value=self.config_store.auto_fetch_after_import)
        self.concise_mode_var = tk.BooleanVar(value=self.config_store.concise_mode)
        self.keyword_var = tk.StringVar()
        self.sender_var = tk.StringVar()
        self.account_search_var = tk.StringVar()
        self.account_group_var = tk.StringVar(value="unused")
        self.status_var = tk.StringVar(value="就绪")
        self.total_badge_var = tk.StringVar(value="共 0 封")
        self.graph_badge_var = tk.StringVar(value="Graph: 0")
        self.imap_badge_var = tk.StringVar(value="IMAP: 0")

        self.build_ui()
        self.account_search_var.trace_add("write", lambda *_args: self.refresh_accounts(reset_scroll=True))
        self.refresh_accounts()
        self.after(120, self.drain_events)

    def build_ui(self) -> None:
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=20, pady=14)

        header = tk.Frame(outer, bg=PANEL, padx=22, pady=10, highlightbackground=BORDER, highlightthickness=1)
        header.pack(fill="x")
        icon = tk.Label(header, text="✉", bg="#2298e6", fg="white", font=("Segoe UI Symbol", 22, "bold"), width=2)
        icon.pack(side="left")
        title_box = tk.Frame(header, bg=PANEL)
        title_box.pack(side="left", padx=(12, 0))
        tk.Label(title_box, text=DISPLAY_NAME, bg=PANEL, fg=TEXT, font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        tk.Label(title_box, text="IMAP OAuth2 + Graph API 双协议", bg=PANEL, fg=MUTED, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(2, 0))
        self.status_label = tk.Label(
            header,
            textvariable=self.status_var,
            bg=GREEN_BG,
            fg=GREEN,
            padx=14,
            pady=7,
            anchor="e",
            justify="right",
            wraplength=520,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.status_label.pack(side="right", fill="x", padx=(12, 0))

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True, pady=(12, 0))

        left = tk.Frame(body, bg=PANEL, width=440, padx=22, pady=20, highlightbackground=BORDER, highlightthickness=1)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(18, 0))

        top_line = tk.Frame(left, bg=PANEL)
        top_line.pack(fill="x")
        tk.Label(top_line, text="♙  邮箱列表", bg=PANEL, fg=TEXT, font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        self.account_count_label = tk.Label(top_line, text="0", bg="#dceaff", fg=BLUE, padx=10, pady=4, font=("Microsoft YaHei UI", 9, "bold"))
        self.account_count_label.pack(side="right")

        account_search = make_search_box(left, self.account_search_var, "邮箱搜索")
        account_search.pack(fill="x", pady=(16, 10))

        group_line = tk.Frame(left, bg=PANEL)
        group_line.pack(fill="x", pady=(0, 10))
        self.unused_button = make_button(group_line, "未使用", lambda: self.set_account_group("unused"), bg=BLUE, fg="white", width=7)
        self.unused_button.pack(side="left", fill="x", expand=True)
        self.used_button = make_button(group_line, "已使用", lambda: self.set_account_group("used"), bg="#eaf2ff", fg=BLUE, width=7)
        self.used_button.pack(side="left", fill="x", expand=True, padx=(10, 0))

        make_button(left, "+  批量导入邮箱", self.open_import_dialog, bg=BLUE, fg="white").pack(fill="x", pady=(4, 10))
        make_button(left, "⇩  导出邮箱", self.export_accounts, bg="#f4faff", fg=TEXT).pack(fill="x")

        select_line = tk.Frame(left, bg=PANEL)
        select_line.pack(fill="x", pady=(18, 8))
        self.select_all_var = tk.BooleanVar(value=True)
        RedCheck(select_line, self.select_all_var, text="全选", command=self.toggle_all_accounts, bg=PANEL, fg=MUTED).pack(side="left")
        make_button(select_line, "删除选中", self.remove_selected, bg="#fff7f7", fg=RED).pack(side="right")

        usage_line = tk.Frame(left, bg=PANEL)
        usage_line.pack(fill="x", pady=(0, 12))
        make_button(usage_line, "标记已使用", self.mark_selected_used, bg="#edf7ff", fg=BLUE, width=8).pack(side="left", fill="x", expand=True)
        make_button(usage_line, "取消标记", self.mark_selected_unused, bg="#f4faff", fg=TEXT, width=8).pack(side="left", fill="x", expand=True, padx=(10, 0))

        left_footer = tk.Frame(left, bg=PANEL)
        left_footer.pack(side="bottom", fill="x", pady=(12, 0))
        make_button(left_footer, "清空全部", self.clear_accounts, bg="#f4faff", fg=TEXT).pack(anchor="w")

        self.account_scroll = ScrollFrame(left, PANEL)
        self.account_scroll.pack(fill="both", expand=True)

        controls = tk.Frame(right, bg=PANEL, padx=18, pady=14, highlightbackground=BORDER, highlightthickness=1)
        controls.pack(fill="x")
        row1 = tk.Frame(controls, bg=PANEL)
        row1.pack(fill="x")
        count_box = tk.Frame(row1, bg="white", padx=10, pady=5, highlightbackground="#8fb8ff", highlightthickness=2)
        count_box.pack(side="right", fill="y")
        count_text = tk.Frame(count_box, bg="white")
        count_text.pack(side="left", fill="y", padx=(0, 8))
        tk.Label(count_text, text="每箱取件数", bg="white", fg=TEXT, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        tk.Label(count_text, text="每个邮箱最多读取", bg="white", fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        top_menu = tk.OptionMenu(count_box, self.top_var, "1", "5", "10", "20", "30", command=lambda _v: self.save_config())
        top_menu.config(bg="#eef6ff", fg=TEXT, activebackground="#dbeafe", relief="flat", bd=0, highlightthickness=0, width=4, font=("Microsoft YaHei UI", 10, "bold"))
        top_menu.pack(side="right", ipady=4)
        keyword = make_search_box(row1, self.keyword_var, "邮件搜索", command=self.render_results)
        keyword.pack(side="left", fill="x", expand=True, padx=(0, 12))
        sender = make_search_box(row1, self.sender_var, "发件人搜索", command=self.render_results)
        sender.pack(side="left", fill="x", expand=True, padx=(0, 12))

        row2 = tk.Frame(controls, bg=PANEL)
        row2.pack(fill="x", pady=(12, 0))
        self.imap_button = make_button(row2, "IMAP令牌", lambda: self.set_protocol("IMAP"), bg="#e7f0ff", fg=BLUE, width=11)
        self.imap_button.pack(side="left")
        self.graph_button = make_button(row2, "Graph令牌", lambda: self.set_protocol("Graph"), bg="#e7f0ff", fg=BLUE, width=11)
        self.graph_button.pack(side="left", padx=(10, 0))
        RedCheck(row2, self.auto_fetch_var, text="导入后自动取件", command=self.save_config, bg=PANEL, fg=MUTED).pack(side="left", padx=(18, 0))
        RedCheck(row2, self.concise_mode_var, text="简洁模式", command=self.save_config, bg=PANEL, fg=MUTED).pack(side="left", padx=(14, 0))

        row3 = tk.Frame(controls, bg=PANEL)
        row3.pack(fill="x", pady=(12, 0))
        make_button(row3, "导出CSV", self.export_csv, bg="#eef6ff", fg=TEXT).pack(side="left")
        make_button(row3, "停止", self.request_stop, bg="#eef6ff", fg=TEXT).pack(side="left", padx=(10, 0))
        make_button(row3, "⇩  全部取件", self.fetch_all, bg=BLUE_DARK, fg="white").pack(side="right")
        make_button(row3, "⇩  选中取件", self.fetch_selected, bg=BLUE, fg="white").pack(side="right", padx=(0, 10))

        self.progress_frame = tk.Frame(right, bg=BG)
        self.progress_frame.pack(fill="x", pady=(10, 0))
        self.progress_label = tk.Label(self.progress_frame, textvariable=self.progress_text_var, bg=BG, fg=TEXT, font=("Microsoft YaHei UI", 9, "bold"))
        self.progress_label.pack(fill="x", anchor="w")
        self.progress_bar = tk.Canvas(self.progress_frame, height=8, bg="#e7eefb", highlightthickness=0)
        self.progress_bar.pack(fill="x", pady=(6, 0))
        self.progress_bar.bind("<Configure>", lambda _e: self.draw_progress())
        self.progress_frame.pack_forget()

        self.result_header = tk.Frame(right, bg=BG)
        self.result_header.pack(fill="x", pady=(12, 8))
        tk.Label(self.result_header, text="✉  取件结果", bg=BG, fg=TEXT, font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        RoundBadge(self.result_header, self.total_badge_var, bg="#dce6ff", fg=BLUE).pack(side="right")
        RoundBadge(self.result_header, self.imap_badge_var, bg="#e0f2fe", fg="#0284c7").pack(side="right", padx=(0, 10))
        RoundBadge(self.result_header, self.graph_badge_var, bg=GREEN_BG, fg="#059669").pack(side="right", padx=(0, 10))

        self.result_scroll = ScrollFrame(right, BG)
        self.result_scroll.pack(fill="both", expand=True)

        self.log_box = None
        self.log("已就绪。默认使用 Graph；导入四段内容后会加密保存在本机。")
        self.update_account_group_buttons()
        self.update_protocol_buttons()

    def save_config(self) -> bool:
        try:
            top = int(self.top_var.get())
        except ValueError:
            top = 10
        self.config_store.client_id = self.client_id_var.get().strip()
        self.config_store.tenant = self.tenant_var.get()
        self.config_store.protocol = self.protocol_var.get()
        self.config_store.top = max(1, min(top, 50))
        self.config_store.auto_fetch_after_import = self.auto_fetch_var.get()
        self.config_store.concise_mode = self.concise_mode_var.get()
        self.top_var.set(str(self.config_store.top))
        self.config_store.save()
        self.graph = None
        return True

    def set_protocol(self, protocol: str) -> None:
        self.protocol_var.set(protocol)
        self.save_config()
        self.update_protocol_buttons()

    def update_protocol_buttons(self) -> None:
        for name, button in (("IMAP", self.imap_button), ("Graph", self.graph_button)):
            active = self.protocol_var.get() == name
            label = f"{name}令牌"
            button.configure(
                text=f"当前 {label}" if active else label,
                bg=BLUE if active else "#eaf2ff",
                fg="white" if active else BLUE,
            )

    def ensure_graph(self) -> GraphMailClient:
        if self.graph is None:
            self.graph = GraphMailClient(self.config_store, self.account_store)
        return self.graph

    def open_import_dialog(self) -> None:
        ImportDialog(self)

    def set_account_group(self, group: str) -> None:
        self.account_group_var.set(group)
        self.update_account_group_buttons()
        self.refresh_accounts(reset_scroll=True)

    def update_account_group_buttons(self) -> None:
        active = self.account_group_var.get()
        for group, button in (("unused", self.unused_button), ("used", self.used_button)):
            button.configure(bg=BLUE if active == group else "#eaf2ff", fg="white" if active == group else BLUE)

    def filtered_accounts(self) -> list[AccountRecord]:
        needle = self.account_search_var.get().strip().lower()
        show_used = self.account_group_var.get() == "used"
        pool = [account for account in self.account_store.accounts if account.used == show_used]
        if not needle:
            return pool
        starts = [account for account in pool if account.email.lower().startswith(needle)]
        contains = [
            account
            for account in pool
            if needle in account.email.lower() and account not in starts
        ]
        return starts + contains

    def refresh_accounts(self, reset_scroll: bool = False) -> None:
        for child in self.account_scroll.inner.winfo_children():
            child.destroy()
        accounts = self.filtered_accounts()
        self.account_count_label.configure(text=f"{len(accounts)}/{len(self.account_store.accounts)}")
        for account in accounts:
            var = self.account_vars.setdefault(account.email, tk.BooleanVar(value=True))
            row = tk.Frame(self.account_scroll.inner, bg="#e8f2ff", padx=10, pady=10, highlightbackground="#bcd5ff", highlightthickness=1)
            row.pack(fill="x", pady=(0, 10))
            RedCheck(row, var, bg="#e8f2ff").pack(side="left")
            make_copy_button(row, lambda email_address=account.email: self.copy_email(email_address)).pack(side="right", padx=(8, 0))
            txt = tk.Frame(row, bg="#e8f2ff")
            txt.pack(side="left", fill="x", expand=True)
            tk.Label(txt, text=account.email, bg="#e8f2ff", fg=TEXT, anchor="w", justify="left", wraplength=280, font=("Microsoft YaHei UI", 10)).pack(anchor="w", fill="x")
            usage_text = "已使用" if account.used else "未使用"
            tk.Label(txt, text=f"{usage_text} · {account.source} · {account.last_status}", bg="#e8f2ff", fg=MUTED, anchor="w", justify="left", wraplength=280, font=("Microsoft YaHei UI", 8)).pack(anchor="w", fill="x")
            self.account_scroll.bind_mousewheel_recursive(row)
        if reset_scroll:
            self.account_scroll.canvas.yview_moveto(0)

    def copy_email(self, email_address: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(email_address)
        self.update()
        self.status_var.set("已复制")
        self.log(f"已复制邮箱：{email_address}")

    def visible_account_emails(self) -> set[str]:
        return {account.email for account in self.filtered_accounts()}

    def selected_emails(self) -> list[str]:
        visible = self.visible_account_emails()
        return [email for email, var in self.account_vars.items() if email in visible and var.get() and self.account_store.get(email)]

    def toggle_all_accounts(self) -> None:
        state = self.select_all_var.get()
        visible = self.visible_account_emails()
        for email, var in self.account_vars.items():
            if email in visible:
                var.set(state)

    def remove_selected(self) -> None:
        selected = set(self.selected_emails())
        if not selected:
            return
        if not messagebox.askyesno("删除选中", f"确定删除选中的 {len(selected)} 个邮箱吗？"):
            return
        removed = self.account_store.remove(selected)
        for email_address in selected:
            self.account_vars.pop(email_address, None)
        self.refresh_accounts()
        self.log(f"已删除 {removed} 个邮箱。")

    def mark_selected_used(self) -> None:
        self.set_selected_usage(True)

    def mark_selected_unused(self) -> None:
        self.set_selected_usage(False)

    def set_selected_usage(self, used: bool) -> None:
        selected = set(self.selected_emails())
        if not selected:
            return
        changed = self.account_store.set_used(selected, used)
        target = "已使用" if used else "未使用"
        for email_address in selected:
            var = self.account_vars.get(email_address)
            if var:
                var.set(False)
        self.refresh_accounts(reset_scroll=True)
        self.log(f"已将 {changed} 个邮箱移动到{target}。")

    def clear_accounts(self) -> None:
        if not self.account_store.accounts:
            return
        if not messagebox.askyesno("清空全部", "确定清空全部邮箱吗？"):
            return
        total = self.account_store.clear()
        self.account_vars.clear()
        self.refresh_accounts()
        self.log(f"已清空 {total} 个邮箱。")

    def export_accounts(self) -> None:
        if not self.account_store.accounts:
            messagebox.showinfo("没有邮箱", "当前没有可导出的邮箱。")
            return
        path = filedialog.asksaveasfilename(title="导出邮箱", defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if not path:
            return
        accounts = sorted(self.account_store.accounts, key=lambda account: account.email.lower())
        lines = [
            "----".join([account.email, account.password, account.client_id, account.refresh_token])
            for account in accounts
        ]
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        self.log(f"已导出 {len(lines)} 个邮箱到 {path}")

    def authorize_selected(self) -> None:
        selected = self.selected_emails()
        if not selected:
            messagebox.showinfo("请选择邮箱", "请先勾选邮箱。")
            return
        self._run_worker(self._authorize_worker, selected)

    def _authorize_worker(self, emails: list[str]) -> None:
        graph = self.ensure_graph()
        for email_address in emails:
            if self.stop_requested.is_set():
                break
            try:
                self.events.put(("status", f"授权 {email_address}"))
                result = graph.authorize(email_address)
                if "access_token" not in result:
                    raise RuntimeError(result.get("error_description") or result.get("error") or "授权失败")
                self.account_store.mark(email_address, "已授权")
                self.events.put(("account", None))
            except Exception as exc:
                self.account_store.mark(email_address, "授权失败")
                self.events.put(("log", f"{email_address} 授权失败：{exc}"))
        self.events.put(("status", "就绪"))

    def fetch_selected(self) -> None:
        selected = self.selected_emails()
        if not selected:
            messagebox.showinfo("请选择邮箱", "请先勾选要取件的邮箱。")
            return
        self.fetch_accounts(selected)

    def fetch_all(self) -> None:
        self.fetch_accounts([account.email for account in self.account_store.accounts])

    def fetch_accounts(self, emails: list[str]) -> None:
        if not emails:
            messagebox.showinfo("没有邮箱", "请先导入邮箱。")
            return
        self.save_config()
        self.stop_requested.clear()
        self.clear_results()
        self._run_worker(self._fetch_worker, emails)

    def clear_results(self) -> None:
        self.mail_rows.clear()
        self.render_results()

    def _fetch_worker(self, emails: list[str]) -> None:
        protocol = self.config_store.protocol
        concise_mode = self.config_store.concise_mode
        top = 1 if concise_mode else self.config_store.top
        accounts = [account for email_address in emails if (account := self.account_store.get(email_address))]
        if not accounts:
            self.events.put(("status", "没有可取件账号"))
            return
        if protocol != "IMAP":
            self.ensure_graph()

        success = total = completed = 0
        max_workers = min(12, len(accounts))
        self.events.put(("progress_start", len(accounts)))
        self.events.put(("status", f"并发取件中：0/{len(accounts)}"))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self.fetch_one_account, account, protocol, top, concise_mode): account for account in accounts}
            for future in as_completed(futures):
                account = futures[future]
                completed += 1
                if self.stop_requested.is_set():
                    for pending in futures:
                        pending.cancel()
                    self.events.put(("log", "已停止，剩余邮箱未继续取件。"))
                    break
                try:
                    rows = future.result()
                    success += 1
                    total += len(rows)
                    self.account_store.mark(account.email, f"成功 {len(rows)} 封", fetched=True)
                    self.events.put(("mail_rows", rows))
                    self.events.put(("account", None))
                    self.events.put(("log", f"{account.email} 获取成功：{len(rows)} 封。"))
                except Exception as exc:
                    self.account_store.mark(account.email, "获取失败")
                    self.events.put(("account", None))
                    self.events.put(("log", f"{account.email} 获取失败：{exc}"))
                self.events.put(("progress", {"done": completed, "total": len(accounts), "account": account.email, "success": success, "messages": total}))
                self.events.put(("status", f"并发取件中：{completed}/{len(accounts)}，已取 {total} 封"))
        self.events.put(("progress_done", None))
        self.events.put(("status", f"完成：{success}/{len(accounts)} 个账号，{total} 封邮件"))

    def fetch_one_account(self, account: AccountRecord, protocol: str, top: int, concise_mode: bool = False) -> list[dict]:
        if protocol == "IMAP":
            rows = self.imap.latest_messages(account, top)
            return [self.concise_row(row) for row in rows] if concise_mode else rows
        messages = self.ensure_graph().latest_messages(account, top)
        rows = [self.graph_row(account.email, message) for message in messages]
        return [self.concise_row(row) for row in rows] if concise_mode else rows

    def concise_row(self, row: dict) -> dict:
        code = extract_verification_code(row.get("subject", ""), row.get("preview", ""))
        return {
            "account": row.get("account", ""),
            "protocol": row.get("protocol", ""),
            "time": row.get("time", ""),
            "sender": row.get("sender", ""),
            "subject": f"验证码：{code}" if code else "未识别到验证码",
            "read": row.get("read", ""),
            "preview": "",
            "webLink": row.get("webLink", ""),
            "code": code,
            "concise": True,
        }

    def graph_row(self, account: str, message: dict) -> dict:
        sender_obj = message.get("from") or message.get("sender") or {}
        email_obj = sender_obj.get("emailAddress") or {}
        return {
            "account": account,
            "protocol": "GRAPH",
            "time": fmt_dt(message.get("receivedDateTime", "")),
            "sender": email_obj.get("address") or email_obj.get("name") or "",
            "subject": message.get("subject") or "",
            "read": "是" if message.get("isRead") else "否",
            "preview": message.get("bodyPreview") or "",
            "webLink": message.get("webLink") or "",
        }

    def request_stop(self) -> None:
        self.stop_requested.set(True)
        self.log("已请求停止。")

    def drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "log":
                    self.log(str(payload))
                elif kind == "account":
                    pass
                elif kind == "mail_rows":
                    self.mail_rows.extend(payload)
                    self.schedule_render_results()
                elif kind == "progress_start":
                    self.show_progress(0, int(payload), "准备取件...")
                elif kind == "progress":
                    done = int(payload["done"])
                    total = int(payload["total"])
                    self.show_progress(done, total, f"[{done}/{total}] {payload['account']}，已取 {payload['messages']} 封")
                elif kind == "progress_done":
                    self.refresh_accounts()
                    self.after(900, self.hide_progress)
        except queue.Empty:
            pass
        self.after(120, self.drain_events)

    def schedule_render_results(self) -> None:
        if self.render_pending:
            return
        self.render_pending = True
        self.after(180, self._render_results_scheduled)

    def _render_results_scheduled(self) -> None:
        self.render_pending = False
        self.render_results()

    def show_progress(self, done: int, total: int, text: str) -> None:
        self.progress_frame.pack(fill="x", pady=(10, 0), before=self.result_header)
        self.progress_text_var.set(text)
        self.progress_var.set(0 if total <= 0 else min(100, max(0, done / total * 100)))
        self.draw_progress()

    def draw_progress(self) -> None:
        if not hasattr(self, "progress_bar"):
            return
        self.progress_bar.delete("all")
        width = max(self.progress_bar.winfo_width(), 1)
        height = max(self.progress_bar.winfo_height(), 8)
        filled = int(width * self.progress_var.get() / 100)
        rounded_rect(self.progress_bar, 0, 0, width, height, 4, fill="#e7eefb", outline="")
        if filled > 0:
            rounded_rect(self.progress_bar, 0, 0, filled, height, 4, fill="#20b7cf", outline="")

    def hide_progress(self) -> None:
        self.progress_frame.pack_forget()

    def render_results(self) -> None:
        for child in self.result_scroll.inner.winfo_children():
            child.destroy()
        keyword = self.keyword_var.get().strip().lower()
        sender_filter = self.sender_var.get().strip().lower()
        rows = []
        for row in self.mail_rows:
            haystack = f"{row.get('subject', '')} {row.get('preview', '')}".lower()
            sender = row.get("sender", "").lower()
            if keyword and keyword not in haystack:
                continue
            if sender_filter and sender_filter not in sender:
                continue
            rows.append(row)
        self.graph_count = sum(1 for row in rows if row.get("protocol") == "GRAPH")
        self.imap_count = sum(1 for row in rows if row.get("protocol") == "IMAP")
        self.total_badge_var.set(f"共 {len(rows)} 封")
        self.graph_badge_var.set(f"Graph: {self.graph_count}")
        self.imap_badge_var.set(f"IMAP: {self.imap_count}")
        for row in rows:
            self.add_result_card(row)
        self.result_scroll.update_scrollregion()
        self.result_scroll.canvas.yview_moveto(0)

    def add_result_card(self, row: dict) -> None:
        preview = compact_text(row.get("preview", ""), 180)
        subject = compact_text(row.get("subject", "") or "(无主题)", 120)
        card = tk.Frame(self.result_scroll.inner, bg=CARD, padx=22, pady=14, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 10))
        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text=short_sender(row.get("sender", "")), bg=CARD, fg="#475569", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        tk.Label(top, text=row.get("protocol", ""), bg=GREEN_BG, fg="#059669", padx=8, pady=3, font=("Microsoft YaHei UI", 8, "bold")).pack(side="left", padx=(10, 0))
        tk.Label(top, text=row.get("time", ""), bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="right")
        if row.get("concise"):
            code_text = row.get("code") or "未识别"
            tk.Label(card, text=f"验证码：{code_text}", bg=CARD, fg=RED if not row.get("code") else TEXT, anchor="w", justify="left", wraplength=1180, font=("Microsoft YaHei UI", 16, "bold")).pack(fill="x", pady=(8, 4))
        else:
            tk.Label(card, text=subject, bg=CARD, fg=TEXT, anchor="w", justify="left", wraplength=1180, font=("Microsoft YaHei UI", 11, "bold")).pack(fill="x", pady=(8, 4))
            tk.Label(card, text=preview, bg=CARD, fg=MUTED, anchor="w", justify="left", wraplength=1180, font=("Microsoft YaHei UI", 9)).pack(fill="x")
        tk.Label(card, text=f"⚑ {row.get('account', '')}", bg=CARD, fg=MUTED, anchor="w", justify="left", wraplength=1180, font=("Microsoft YaHei UI", 8)).pack(fill="x", pady=(8, 0))
        self.bind_result_card(card, row)
        self.result_scroll.bind_mousewheel_recursive(card)

    def bind_result_card(self, widget, row: dict) -> None:
        widget.bind("<Button-1>", lambda _e, item=row: DetailDialog(self, item), add="+")
        widget.configure(cursor="hand2")
        for child in widget.winfo_children():
            self.bind_result_card(child, row)

    def export_csv(self) -> None:
        if not self.mail_rows:
            messagebox.showinfo("没有结果", "当前没有可导出的邮件结果。")
            return
        path = filedialog.asksaveasfilename(title="导出结果", defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["account", "protocol", "time", "sender", "subject", "code", "read", "preview", "webLink", "concise"], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.mail_rows)
        self.log(f"已导出 {len(self.mail_rows)} 条结果到 {path}")

    def _run_worker(self, target, *args) -> None:
        threading.Thread(target=target, args=args, daemon=True).start()

    def log(self, text: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
        self.logs.append(line)
        if self.log_box:
            self.log_box.insert(tk.END, line + "\n")
            self.log_box.see(tk.END)


def short_sender(sender: str) -> str:
    if "<" in sender:
        return sender.split("<", 1)[0].strip().strip('"') or sender
    if "@" in sender:
        return sender.split("@", 1)[0]
    return sender or "(未知发件人)"


def compact_text(value: str, limit: int) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def extract_verification_code(*parts: str) -> str:
    text = " ".join(part or "" for part in parts)
    for pattern in CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return ""


if __name__ == "__main__":
    enable_dpi_awareness()
    MailFetcherApp().mainloop()
