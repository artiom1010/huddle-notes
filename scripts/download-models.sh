#!/usr/bin/env bash
# Download the whisper and voice models into $HUDDLE_MODELS_DIR (default: $HUDDLE_DATA_DIR/models)
# and check them against known checksums, so a tampered or broken file never gets used.
set -euo pipefail

dir="${HUDDLE_MODELS_DIR:-${HUDDLE_DATA_DIR:-data}/models}"
mkdir -p "$dir"

sha256() {
  if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

fetch() {
  local name=$1 url=$2 sha=$3
  if [[ -f "$dir/$name" && "$(sha256 "$dir/$name")" == "$sha" ]]; then
    echo "$name: ok"
    return
  fi
  echo "$name: downloading"
  curl -fL --retry 3 --output "$dir/$name.partial" "$url"
  if [[ "$(sha256 "$dir/$name.partial")" != "$sha" ]]; then
    rm -f "$dir/$name.partial"
    echo "$name: checksum mismatch, not used" >&2
    exit 1
  fi
  mv "$dir/$name.partial" "$dir/$name"
}

fetch ggml-large-v3-turbo.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin \
  1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69
fetch wespeaker_en_voxceleb_resnet34.onnx \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx \
  5ef208a9da1453335308a6b6f4e6dfbd7e183a38b604de0a57664f45d257fe94
