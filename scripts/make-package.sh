#!/usr/bin/env bash
# Build the "Huddle Notes" folder for one person's computer, and a zip of it.
#
#   scripts/make-package.sh <team data folder> <output folder> <Slack channel ID> <Slack member ID>
#
# The team data folder holds people/ (faces, voices) and glossaries/. They are biometric
# and private: hand the zip to its owner directly, never through a shared channel.
set -euo pipefail
cd "$(dirname "$0")/.."

data="${1:?team data folder}"
out="${2:?output folder}/Huddle Notes"
channel="${3:?Slack channel ID}"
member="${4:?Slack member ID}"
[[ -d "$data/people" && -d "$data/glossaries" ]] || { echo "$data needs people/ and glossaries/" >&2; exit 1; }
[[ ! -e "$out" ]] || { echo "$out already exists" >&2; exit 1; }

mkdir -p "$out/программа" "$out/данные/logs" "$out/Записи" "$out/Результаты" "$out/whisper"
git archive HEAD | tar -x -C "$out/программа"
cp -R "$data/people" "$data/glossaries" "$out/данные/"
cp package/CLAUDE.md "$out/CLAUDE.md"
sed -e "s/{{SLACK_CHANNEL}}/$channel/" -e "s/{{SLACK_NOTIFY_USER}}/$member/" package/env.template > "$out/.env"

# zip through Python: it marks names as UTF-8, so Windows shows «Записи», not mojibake
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
echo "$(dirname "$out")/Huddle Notes.zip"
