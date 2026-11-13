#!/usr/bin/env bash
# Build the "Call Recaps" folder for one person's computer, and a zip of it.
#
#   scripts/make-package.sh <team data folder> <output folder> [<Slack channel ID> [<Slack member ID>]]
#
# Without a channel the package is for anyone: the owner gives the ID of their own channel
# while Claude installs it (one channel, one installation; see CLAUDE.md, step 6).
# The zip is named with the build date («Call Recaps 2026-11-13.zip»); inside it is the
# folder «Call Recaps».
#
# From the team data folder only the glossary named in package/env.template is taken: other
# teams' glossaries stay out. Faces and voices (people/) stay out too:
# speakers are not named by default (HUDDLE_SPEAKERS=off), and they are biometric data.
# No token goes in: the owner writes it into .env. With a channel and a member ID the zip
# holds them too.
set -euo pipefail
cd "$(dirname "$0")/.."

data="${1:?team data folder}"
out="${2:?output folder}/Call Recaps"
zip="$(dirname "$out")/Call Recaps $(date +%Y-%m-%d).zip"  # no dots: they would read as an extension
channel="${3:-}"
member="${4:-}"
[[ ! -e "$out" && ! -e "$zip" ]] || { echo "$out or $zip already exists" >&2; exit 1; }
[[ "$channel$member" =~ ^[A-Z0-9]*$ ]] || { echo "Slack IDs are letters and digits, e.g. C0123ABC" >&2; exit 1; }
# C0CEZN01JTV (#call-recaps) belongs to an installation already: one channel, one installation
[[ "$channel" != C0CEZN01JTV ]] || { echo "C0CEZN01JTV is taken: give the package its own channel, or none" >&2; exit 1; }
# программа/ comes from the last commit, CLAUDE.md and .env from the files here: they must match
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "commit the changes first" >&2; exit 1; }
glossary="$(sed -n 's/^HUDDLE_GLOSSARY=//p' package/env.template)"
[[ -n "$glossary" && -s "$data/glossaries/$glossary.txt" ]] || { echo "no $data/glossaries/$glossary.txt" >&2; exit 1; }

mkdir -p "$out/программа" "$out/данные/logs" "$out/данные/glossaries" "$out/Записи" "$out/Результаты" "$out/whisper"
git archive HEAD | tar -x -C "$out/программа"
cp "$data/glossaries/$glossary.txt" "$out/данные/glossaries/"
cp package/CLAUDE.md "$out/CLAUDE.md"
sed -e "s/{{SLACK_CHANNEL}}/$channel/" -e "s/{{SLACK_NOTIFY_USER}}/$member/" package/env.template > "$out/.env"

# zip through Python: it marks names as UTF-8, so Windows shows «Записи», not mojibake
python3 - "$out" "$zip" <<'PY'
import sys, unicodedata, zipfile
from pathlib import Path
root, target = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(root.rglob("*")):
        name = unicodedata.normalize("NFC", str(path.relative_to(root.parent)))
        z.write(path, name + ("/" if path.is_dir() else ""))
PY
echo "$out"
echo "$zip"
