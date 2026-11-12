"""The note on screen when a recording is taken: what reaches PowerShell, and Windows itself.

The tests marked windows run only there (the Windows job in CI): Windows builds the toast
from our script, and the app id goes into the user's registry.
"""

import base64
import contextlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from huddle_notes.config import ConfigError, Settings
from huddle_notes.notify import APP_ID, POWERSHELL_APP_ID, WindowsToast, desktop_notifier, register, toast_xml

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

NAME = "2026-11-12 «Трактор» & <Вася> 'O'Brien' ‘кавычки’.mkv"


def decoded_script(command: list[str]) -> str:
    return base64.b64decode(command[-1]).decode("utf-16-le")


def payloads(script: str) -> list[str]:
    return [base64.b64decode(b).decode("utf-8") for b in re.findall(r"Decode '([A-Za-z0-9+/=]+)'", script)]


class FakeRun:
    def __init__(self, returncode: int = 0, stderr: str = "", error: Exception | None = None) -> None:
        self.returncode, self.stderr, self.error, self.calls = returncode, stderr, error, []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)


def test_the_toast_says_which_recording_and_where_the_notes_will_be():
    run = FakeRun()
    WindowsToast(slack=True, run=run).taken(NAME)
    xml, app = payloads(decoded_script(run.calls[0][0]))
    texts = [t.text for t in ET.fromstring(xml).iter("text")]  # noqa: S314 (our own XML)
    assert texts == ["Взял в работу", NAME, "Заметки придут в Slack, когда будут готовы"]
    assert app == APP_ID

    run = FakeRun()
    WindowsToast(slack=False, run=run).taken(NAME)
    assert "«Результаты»" in payloads(decoded_script(run.calls[0][0]))[0]


def test_the_name_reaches_powershell_whole_whatever_its_letters_and_quotes():
    command = WindowsToast(slack=True).command(toast_xml("Взял в работу", NAME))
    assert all(arg.isascii() for arg in command)  # nothing for Windows' ANSI code page to garble
    assert command[0].endswith("powershell.exe")
    assert command[1:4] == ["-NoProfile", "-NonInteractive", "-EncodedCommand"]
    script = decoded_script(command)
    assert NAME not in script and "Вася" not in script  # only base64 inside PowerShell's quotes
    assert ".Show($toast)" in script


def test_a_name_utf8_cannot_hold_is_logged_not_raised(caplog):
    assert WindowsToast(slack=True, run=FakeRun()).show("Взял в работу", "bad \udc80 name.mkv") is False
    assert "notification failed" in caplog.text


def test_powershell_errors_come_back_as_one_plain_line():
    script = decoded_script(WindowsToast(slack=True).command(toast_xml("x")))
    assert "$ProgressPreference = 'SilentlyContinue'" in script  # no CLIXML progress records on stderr
    assert "[Console]::Error.WriteLine($_.Exception.Message)" in script and script.rstrip().endswith("}")
    run = FakeRun()
    WindowsToast(slack=True, run=run).show("x")
    assert run.calls[0][1]["encoding"] == "utf-8"


def test_a_check_builds_the_toast_without_showing_it():
    script = decoded_script(WindowsToast(slack=True).command(toast_xml("x"), show=False))
    assert "LoadXml" in script and "Show(" not in script


def test_a_toast_windows_refused_is_logged_not_raised(caplog):
    assert WindowsToast(slack=True, run=FakeRun(returncode=1, stderr="Element not found.")).show("x") is False
    assert "Element not found" in caplog.text
    timeout = subprocess.TimeoutExpired("powershell", 20)
    assert WindowsToast(slack=True, run=FakeRun(error=timeout)).show("x") is False
    assert WindowsToast(slack=True, run=FakeRun(error=FileNotFoundError("powershell.exe"))).show("x") is False


def test_without_a_registered_name_toasts_go_under_powershell():
    assert WindowsToast(slack=True).registered
    assert not WindowsToast(slack=True, app_id=POWERSHELL_APP_ID).registered


@pytest.mark.skipif(sys.platform == "win32", reason="not Windows")
def test_no_notes_on_screen_outside_windows():
    assert desktop_notifier("on", slack=True) is None
    assert desktop_notifier("powershell", slack=True) is None
    assert register("HuddleNotesTest", "x") is False


def test_notes_on_screen_are_on_unless_switched_off(tmp_path):
    base = {"HUDDLE_DATA_DIR": str(tmp_path)}

    def mode(value: str | None) -> str:
        return Settings.from_env(base if value is None else base | {"HUDDLE_DESKTOP_NOTIFY": value}).desktop_notify

    assert mode(None) == mode("") == mode("on") == mode("yes") == "on"
    assert mode("off") == mode("0") == "off"
    assert mode("PowerShell") == "powershell"
    with pytest.raises(ConfigError):
        Settings.from_env(base | {"HUDDLE_DESKTOP_NOTIFY": "maybe"})


@windows
def test_windows_builds_the_toast_from_our_script():
    command = WindowsToast(slack=True).command(toast_xml("Взял в работу", NAME, "Заметки придут в Slack"), show=False)
    done = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=60)
    assert done.returncode == 0, done.stderr


@windows
def test_windows_rejects_a_broken_toast_so_the_check_above_means_something():
    command = WindowsToast(slack=True).command("<toast><visual>", show=False)
    done = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=60)
    assert done.returncode != 0


@windows
def test_powershell_mode_registers_nothing_and_uses_powershells_id():
    toast = desktop_notifier("powershell", slack=True)
    assert toast is not None and toast.app_id == POWERSHELL_APP_ID and not toast.registered


@windows
def test_the_app_name_goes_into_the_users_registry():
    import winreg

    app_id, key_path = "HuddleNotesTest", r"Software\Classes\AppUserModelId\HuddleNotesTest"
    try:
        assert register(app_id, "Huddle Notes Test")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            assert winreg.QueryValueEx(key, "DisplayName")[0] == "Huddle Notes Test"
    finally:
        with contextlib.suppress(FileNotFoundError):  # not created: the assert above says why
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
