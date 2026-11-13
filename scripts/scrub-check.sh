#!/usr/bin/env bash
# scrub-check.sh: the repo carries no secret and no path or host from the deployment it was extracted from.
#   1. gitleaks over the working tree AND every commit (full history), pinned version
#   2. a grep over every tracked file for private deployment markers (home-directory paths, private host names) and
#      for personal and internal names that never belong in public code
# Exits non-zero on any hit. Run from the repo root; CI runs it on every push.
set -euo pipefail
GL_VERSION=8.30.1
gl=$(command -v gitleaks || true)
if [ -z "$gl" ]; then
  d=$(mktemp -d); gl="$d/gitleaks"
  curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GL_VERSION}/gitleaks_${GL_VERSION}_linux_x64.tar.gz" | tar xz -C "$d" gitleaks
fi
rc=0
"$gl" dir . --no-banner --redact || rc=1
if git rev-parse --git-dir >/dev/null 2>&1 && [ "$(git rev-parse --is-shallow-repository)" = false ]; then
  "$gl" git . --no-banner --redact || rc=1
fi
# Written as character classes so this file does not match itself. Extend with $SCRUB_EXTRA (an ERE) for your own.
pat='/hom[e]/|novaserve[r]|tail4016d[7]|[Tt][Yy][Ll][Ee][Rr]|[Yy][Oo][Uu][Kk]|[Mm]oonligh[t]|knowledg[e]#|kxagen[t]|kxde[v]|1000xde[v]|ethd[e]'
[ -n "${SCRUB_EXTRA:-}" ] && pat="$pat|$SCRUB_EXTRA"
if git ls-files -z | xargs -0 grep -nIE "$pat" --; then
  echo "scrub-check: private deployment marker found (above)" >&2; rc=1
else
  echo "scrub-check: 0 private-marker hits in $(git ls-files | wc -l) tracked files"
fi
exit $rc
