"""The PowerShell that package/CLAUDE.md gives Claude for .env, as Claude would run it.

The token is in .env, so Claude changes values with Set-Env instead of reading the file,
and checks the token with a line that prints only True or False. Both must agree with
load_env_file: the first line of a key is the one that counts.

The tests run only on Windows (the Windows job in CI), with Windows PowerShell 5.1.
"""

import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

from huddle_notes.config import load_env_file

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell only")

CLAUDE_MD = Path(__file__).parents[1] / "package" / "CLAUDE.md"
FAKE_TOKEN = "xoxb-1-2-abc"  # noqa: S105 - shaped like a token, never one


def line_of_claude_md(start: str) -> str:
    """The one command in CLAUDE.md that starts so, exactly as Claude reads it there."""
    found = [s.strip() for s in CLAUDE_MD.read_text(encoding="utf-8").splitlines() if s.strip().startswith(start)]
    assert len(found) == 1, found
    return found[0]


def powershell(script: str, cwd: Path) -> str:
    exe = Path(os.environ["SYSTEMROOT"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    done = subprocess.run(
        [str(exe), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def loaded(env_file: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    load_env_file(env_file, env)
    return env


def test_set_env_changes_the_line_that_counts_and_adds_a_missing_one(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HUDDLE_DATA_DIR=данные\nHUDDLE_GLOSSARY=староста\nSLACK_BOT_TOKEN=xoxb-1-2-abc\n"
        "# SLACK_CHANNEL=old, a comment\nSLACK_CHANNEL=\nHUDDLE_TIMEZONE=Europe/Chisinau\n",
        encoding="utf-8",
    )
    calls = "; ".join(
        (
            "Set-Env SLACK_CHANNEL C0123ABC",
            "Set-Env HUDDLE_TIMEZONE Europe/Berlin",
            "Set-Env HUDDLE_DESKTOP_NOTIFY powershell",
        )
    )
    said = powershell(line_of_claude_md("function Set-Env").replace("Set-Env <КЛЮЧ> <значение>", calls), tmp_path)

    assert said == ""  # nothing of .env reaches the chat

    raw = env_file.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert "# SLACK_CHANNEL=old, a comment" in raw.decode("utf-8")
    env = loaded(env_file)
    assert env["SLACK_CHANNEL"] == "C0123ABC"
    assert env["HUDDLE_TIMEZONE"] == "Europe/Berlin"
    assert env["HUDDLE_DESKTOP_NOTIFY"] == "powershell"  # there was no such line: added
    assert env["HUDDLE_DATA_DIR"] == "данные" and env["HUDDLE_GLOSSARY"] == "староста"
    assert env["SLACK_BOT_TOKEN"] == FAKE_TOKEN  # the token line is left as it was


@pytest.mark.parametrize(
    "text",
    [
        "SLACK_BOT_TOKEN=xoxb-1-2-abc\n",
        'SLACK_BOT_TOKEN="xoxb-1-2-abc"\n',
        "SLACK_BOT_TOKEN=\nSLACK_BOT_TOKEN=xoxb-1-2-abc\n",  # the second line does not count
        "SLACK_BOT_TOKEN=\n",
        "SLACK_CHANNEL=C0123ABC\n",
    ],
)
def test_the_token_check_says_what_the_program_will_load(tmp_path: Path, text: str) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(text, encoding="utf-8")
    said = powershell(line_of_claude_md("$l = Select-String"), tmp_path)
    assert said in ("True", "False")  # never the token itself
    assert (said == "True") == loaded(env_file).get("SLACK_BOT_TOKEN", "").startswith("xoxb-")
