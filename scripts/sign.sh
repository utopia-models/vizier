#!/usr/bin/env bash
# sign.sh <in.exe> <out.exe>: Authenticode-sign a Windows binary with osslsigncode (SHA-256, RFC 3161 timestamp),
# then verify the signature against the published certificate in signing/.
#   VIZIER_CODESIGN_PFX_B64       the PKCS#12 (cert + key), base64       (CI secret; never committed)
#   VIZIER_CODESIGN_PFX_PASSWORD  its password                           (CI secret)
#   VIZIER_TIMESTAMP_URL          RFC 3161 server (default http://timestamp.digicert.com)
set -euo pipefail
in=${1:?in.exe}; out=${2:?out.exe}
: "${VIZIER_CODESIGN_PFX_B64:?not set}" "${VIZIER_CODESIGN_PFX_PASSWORD:?not set}"
here=$(cd "$(dirname "$0")/.." && pwd)
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
printf '%s' "$VIZIER_CODESIGN_PFX_B64" | base64 -d > "$d/k.pfx"
printf '%s' "$VIZIER_CODESIGN_PFX_PASSWORD" > "$d/pw"
osslsigncode sign -pkcs12 "$d/k.pfx" -readpass "$d/pw" -h sha256 \
  -n "Vizier" -i "https://github.com/utopia-models/vizier" \
  -ts "${VIZIER_TIMESTAMP_URL:-http://timestamp.digicert.com}" -in "$in" -out "$out"
# verify: the signature chains to our published certificate (self-signed today, so it is its own CA)
osslsigncode verify -CAfile "$here/signing/vizier-codesign.crt" -in "$out"
