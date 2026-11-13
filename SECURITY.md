# Security policy

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting (the **Security** tab →
**Report a vulnerability**) on this repository. We reply within 3 business days and keep you updated until a fix ships.

## Supported versions

Only the latest release receives fixes.

## What Vizier handles, and how

- **Transcripts** of your calls, as posted to the server. It keeps the open call's lines in memory and drops them when
  the call ends or the server stops; the event log (`$VIZIER_LOG`) records timings and counts, not text. A script you
  post is written to `$VIZIER_DATA/posted-script.md`; take recordings go to `$VIZIER_TAKES`.
- **No audio and no model keys.** The server never receives audio and never calls a language model, so it holds no
  API keys. Whatever transcribes and thinks (your agent, a LiveKit transcriber) holds its own.
- **Network exposure**: the server binds `127.0.0.1` by default. Set `VIZIER_TOKEN` and every POST then needs
  `Authorization: Bearer <token>`; GETs (the transcript, the feed) stay open, so beyond localhost also put it behind
  something that authenticates (a VPN, a tailnet, an authenticating reverse proxy). CORS only admits the desktop
  app's origins plus any you add in `VIZIER_ALLOWED_ORIGINS`.
- **LiveKit**: `publish.html` takes a join token, never an API secret; mint tokens on your own server.
- **Signed releases**: `vizier.exe` is Authenticode-signed; verify it against [`signing/`](signing/) before running a
  binary from anywhere other than this repository's Releases page.

## Secrets in this repository

None. CI runs `scripts/scrub-check.sh` (gitleaks over the tree and full history, plus a private-path grep) on every
push.
