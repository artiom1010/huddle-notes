#!/usr/bin/env bash
# Put a released version on this server: ./deploy.sh v0.1.0
#
# Builds the version, checks it on the regression calls, and keeps it only if every call
# is named exactly as accepted. Otherwise the previous version is built back and stays.
set -euo pipefail
cd "$(dirname "$0")"

tag="${1:?usage: ./deploy.sh <version tag, e.g. v0.1.0>}"
[[ -f .env ]] || { echo "no .env: copy .env.example and fill it in" >&2; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || { echo "local changes in the checkout, not deploying" >&2; exit 1; }

previous="$(git rev-parse HEAD)"
git fetch --tags --quiet origin
git -c advice.detachedHead=false checkout --quiet "refs/tags/$tag"

back() {
  echo "$tag failed the check; going back to ${previous:0:8}" >&2
  git -c advice.detachedHead=false checkout --quiet "$previous"
  docker compose build --quiet worker
  exit 1
}

docker compose build --quiet worker || back
docker compose run --rm worker regress /regression || back
echo "$tag deployed: $(date -u +%FT%TZ)" | tee -a deploy.log
