#!/usr/bin/env bash
# Build an update for a "Huddle Notes" folder that is already installed: the program and
# CLAUDE.md only, no team data, so it can be sent like any file.
#
#   scripts/make-update.sh <output folder>
#
# The owner unpacks it anywhere and asks Claude Code in Huddle Notes: «Обнови программу из
# папки <путь>». The steps are in CLAUDE.md, section «Обнови программу».
set -euo pipefail
cd "$(dirname "$0")/.."

out="${1:?output folder}/Обновление Huddle Notes"
[[ ! -e "$out" ]] || { echo "$out already exists" >&2; exit 1; }

mkdir -p "$out/программа"
git archive HEAD | tar -x -C "$out/программа"
cp package/CLAUDE.md "$out/CLAUDE.md"

# zip through Python: it marks names as UTF-8, so Windows shows «программа», not mojibake
python3 - "$out" <<'PY'
import sys, unicodedata, zipfile
from pathlib import Path
root = Path(sys.argv[1])
with zipfile.ZipFile(root.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(root.rglob("*")):
        name = unicodedata.normalize("NFC", str(path.relative_to(root.parent)))
        z.write(path, name + ("/" if path.is_dir() else ""))
PY
echo "$out"
echo "$(dirname "$out")/Обновление Huddle Notes.zip"
