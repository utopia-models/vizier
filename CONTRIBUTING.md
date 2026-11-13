# Contributing to Vizier

Thanks for helping. Vizier is small on purpose: one server file, one app window. Keep changes in that spirit.

## Setup

```bash
cd server && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

Desktop app: `scripts/setup-xwin.sh` then `app/build.sh` (Linux → Windows), or `cargo build --release` in `app/` on
macOS/Windows.

## Before you open a pull request

1. `cd server && pytest -q` passes, and new server behaviour has a test.
2. `scripts/scrub-check.sh` reports zero hits: no secrets, no paths or host names from your own machine. Configuration
   belongs in environment variables or `vizier.env` (never committed), not in code.
3. The app still builds (`app/build.sh` or `cargo build --release`).
4. User-visible changes get a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).

One topic per pull request; describe what you measured, not only what you changed (latency claims need numbers from
`$VIZIER_LOG`).

## Releases

Maintainers bump `version` in `app/Cargo.toml` and `app/tauri.conf.json` (semver), move **Unreleased** in the
changelog under the new version, and push a tag `vX.Y.Z`. CI builds, signs and publishes `vizier.exe` with its
SHA-256.

## License

By contributing you agree that your contribution is licensed under the [Apache License 2.0](LICENSE).
