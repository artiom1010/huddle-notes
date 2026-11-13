#!/usr/bin/env bash
# Build an update for a "Call Recaps" folder that is already installed: the program and
# CLAUDE.md only, no team data, so it can be sent like any file.
#
#   scripts/make-update.sh <output folder>
#
# The name carries the build date («Обновление Call Recaps 2026-11-12»), so two updates
# never share a name and the newer one is plain to see.
#
# The owner unpacks it anywhere and asks Claude Code in Call Recaps: «Обнови программу из
# папки <путь>». The steps are in CLAUDE.md, section «Обнови программу».
set -euo pipefail
cd "$(dirname "$0")/.."

out="${1:?output folder}/Обновление Call Recaps $(date +%Y-%m-%d)"  # no dots: they would read as an extension
[[ ! -e "$out" ]] || { echo "$out already exists" >&2; exit 1; }
# программа/ comes from the last commit, CLAUDE.md from the file here: they must match
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "commit the changes first" >&2; exit 1; }

mkdir -p "$out/программа"
git archive HEAD | tar -x -C "$out/программа"
cp package/CLAUDE.md "$out/CLAUDE.md"

# zip through Python: it marks names as UTF-8, so Windows shows «программа», not mojibake
python3 - "$out" <<'PY'
import sys, unicodedata, zipfile
from pathlib import Path
root = Path(sys.argv[1])
with zipfile.ZipFile(root.parent / f"{root.name}.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(root.rglob("*")):
        name = unicodedata.normalize("NFC", str(path.relative_to(root.parent)))
        z.write(path, name + ("/" if path.is_dir() else ""))
PY
echo "$out"
echo "$out.zip"
