"""A note on the owner's screen the moment a new recording is taken into work.

On Windows it is a toast in the corner of the screen, shown by Windows PowerShell 5.1, which
is part of Windows: nothing to install. Elsewhere nothing is shown. A note that cannot be
shown is logged and forgotten: it must never hold up a call.

The toast is described in XML and the PowerShell script carries it base64-encoded, and the
script itself goes to PowerShell as -EncodedCommand. So a recording's name reaches Windows as
it is, whatever letters and quotes it has: Windows passes command-line arguments in the
system's ANSI code page, which would garble Cyrillic, and PowerShell treats curly quotes like
straight ones.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

log = logging.getLogger(__name__)

APP_ID = "HuddleNotes"
APP_NAME = "Huddle Notes"
# Windows PowerShell's own id: toasts under it need nothing registered, but are headed
# "Windows PowerShell" instead of "Huddle Notes"
POWERSHELL_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
TIMEOUT = 20  # seconds; PowerShell starts in one or two
_NO_WINDOW: Any = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flashing up for a moment

Run = Callable[..., subprocess.CompletedProcess[str]]


class WindowsToast:
    """Toasts under one app id; `slack` says where the notes will turn up."""

    def __init__(self, slack: bool, app_id: str = APP_ID, run: Run = subprocess.run) -> None:
        self.slack = slack
        self.app_id = app_id
        self._run = run

    @property
    def registered(self) -> bool:
        """Headed by our own name, not by Windows PowerShell's."""
        return self.app_id != POWERSHELL_APP_ID

    def taken(self, recording: str) -> None:
        where = "Заметки придут в Slack, когда будут готовы" if self.slack else "Заметки появятся в папке «Результаты»"
        self.show("Взял в работу", recording, where)

    def show(self, *lines: str) -> bool:
        """A title and up to two lines. False, and a line in the log, when it was not shown."""
        try:
            done = self._run(
                self.command(toast_xml(*lines)),
                capture_output=True,
                text=True,
                encoding="utf-8",  # the script switches PowerShell's output to UTF-8
                errors="replace",
                timeout=TIMEOUT,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError, ValueError) as e:  # ValueError: a name UTF-8 can't hold
            log.warning("notification failed: %s", e)
            return False
        if done.returncode != 0:
            log.warning("notification failed: %s", (done.stderr or done.stdout).strip()[:300])
            return False
        return True

    def command(self, xml: str, show: bool = True) -> list[str]:
        """The PowerShell command; with show=False it only builds the toast (a check)."""
        encoded = _encoded(script(xml, self.app_id, show))
        return [_powershell(), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def desktop_notifier(mode: str, slack: bool) -> WindowsToast | None:
    """Toasts on Windows: mode on — under our own name when the id could be registered;
    powershell — under Windows PowerShell's id, for a Windows that drops toasts of an id it
    only knows from the registry (it does so silently, so only the owner's eyes can tell).
    None elsewhere or when off."""
    if mode == "off" or sys.platform != "win32":
        return None
    own = mode == "on" and register(APP_ID, APP_NAME)
    return WindowsToast(slack, APP_ID if own else POWERSHELL_APP_ID)


def register(app_id: str, name: str) -> bool:
    """Windows heads a toast with the name it finds for its app id in the user's registry
    (HKCU: no admin rights). False when it could not be written."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{app_id}") as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, name)
    except OSError as e:
        log.warning("notifications go under Windows PowerShell's name: %s", e)
        return False
    return True


def toast_xml(*lines: str) -> str:
    texts = "".join(f"<text>{escape(line)}</text>" for line in lines[:3])
    return f'<toast><visual><binding template="ToastGeneric">{texts}</binding></visual></toast>'


def script(xml: str, app_id: str, show: bool = True) -> str:
    """Builds the toast from base64 (no quoting to get wrong) and, with show, shows it. An
    error comes out as one plain UTF-8 line on stderr, not as PowerShell's CLIXML."""
    winrt = "ContentType = WindowsRuntime] > $null"
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        "try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}",  # no console: never mind
        "try {",
        f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, {winrt}",
        f"[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, {winrt}",
        f"[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, {winrt}",
        "function Decode($b64) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)) }",
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument",
        f"$doc.LoadXml((Decode '{_b64(xml)}'))",
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $doc",
    ]
    if show:
        manager = "[Windows.UI.Notifications.ToastNotificationManager]"
        lines.append(f"{manager}::CreateToastNotifier((Decode '{_b64(app_id)}')).Show($toast)")
    lines += ["} catch {", "[Console]::Error.WriteLine($_.Exception.Message)", "exit 1", "}"]
    return "\n".join(lines)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _encoded(script: str) -> str:
    """PowerShell's -EncodedCommand: base64 of UTF-16LE."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _powershell() -> str:
    """Windows PowerShell 5.1 by its full path: PowerShell 7 cannot load Windows' toast types,
    and a full path can't be shadowed by another powershell.exe on PATH."""
    root = os.environ.get("SYSTEMROOT", r"C:\Windows")  # Windows reads variable names in any case
    return str(Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
