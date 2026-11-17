#!/usr/bin/env bash
# fetch-voice.sh: put the voice-follow recognizer into app/ui/vendor/ (gitignored; app/build.sh runs this first).
#   vosk.js                                  vosk-browser 0.0.8 (Apache-2.0): Vosk/Kaldi compiled to WebAssembly
#   vosk-model-small-en-us-0.15.tar.gz       the 40 MB English model (Apache-2.0, alphacephei.com/vosk/models)
# Both are pinned by SHA-256 and re-downloaded only when missing or wrong. No account, no key, nothing paid.
set -euo pipefail
dir="$(cd "$(dirname "$0")/.." && pwd)/app/ui/vendor"
mkdir -p "$dir"
VOSK_TGZ=https://registry.npmjs.org/vosk-browser/-/vosk-browser-0.0.8.tgz
VOSK_SHA=29504515526e974f4cb053cf08811c4de5fb2a74007c0a5a957db50eaa8d5d0c      # package/dist/vosk.js
MODEL=vosk-model-small-en-us-0.15
MODEL_URL=https://ccoreilly.github.io/vosk-browser/models/$MODEL.tar.gz       # the model as vosk-browser loads it
MODEL_SHA=f0b24bb92a48ca575b6a96500d6b543f0f079c573dfe85bbe16001fc0404e1d8
ZIP_URL=https://alphacephei.com/vosk/models/$MODEL.zip                         # fallback: the upstream zip, repacked
ZIP_SHA=30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498

ok() { [ -f "$1" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ]; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

if ! ok "$dir/vosk.js" "$VOSK_SHA"; then
  curl -fsSL "$VOSK_TGZ" -o "$tmp/v.tgz"
  tar -xzf "$tmp/v.tgz" -C "$tmp" package/dist/vosk.js
  ok "$tmp/package/dist/vosk.js" "$VOSK_SHA" || { echo "fetch-voice: vosk.js checksum mismatch" >&2; exit 1; }
  mv "$tmp/package/dist/vosk.js" "$dir/vosk.js"
fi
if ! ok "$dir/$MODEL.tar.gz" "$MODEL_SHA"; then
  if curl -fsSL "$MODEL_URL" -o "$tmp/m.tar.gz" && ok "$tmp/m.tar.gz" "$MODEL_SHA"; then
    mv "$tmp/m.tar.gz" "$dir/$MODEL.tar.gz"
  else
    curl -fsSL "$ZIP_URL" -o "$tmp/m.zip"
    ok "$tmp/m.zip" "$ZIP_SHA" || { echo "fetch-voice: model checksum mismatch" >&2; exit 1; }
    python3 -c 'import sys, tarfile, zipfile
z = zipfile.ZipFile(sys.argv[1]); t = tarfile.open(sys.argv[2], "w:gz")
for i in z.infolist():
    ti = tarfile.TarInfo(i.filename); ti.size = 0 if i.is_dir() else i.file_size; ti.type = tarfile.DIRTYPE if i.is_dir() else tarfile.REGTYPE
    t.addfile(ti, None if i.is_dir() else z.open(i))
t.close()' "$tmp/m.zip" "$dir/$MODEL.tar.gz"   # a repack hashes differently from MODEL_SHA; the zip's hash vouches for it
  fi
fi
ls -la "$dir"
