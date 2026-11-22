#!/usr/bin/env bash
# build.sh: cross-compile vizier.exe (Windows x64) from Linux with cargo-xwin, which downloads the MSVC CRT + Windows
# SDK itself. Needs: rustup with target x86_64-pc-windows-msvc, cargo-xwin, and an LLVM whose bin dir provides
# clang-cl, lld-link, llvm-lib and llvm-rc (tauri-winres needs llvm-rc for the icon). scripts/setup-xwin.sh installs
# all of it on Debian/Ubuntu. Output: one self-contained vizier.exe (WebView2 loader linked in).
# macOS / Windows hosts: `cargo build --release` in this directory.
set -euo pipefail
cd "$(dirname "$0")"
for d in /usr/lib/llvm-*/bin; do [ -x "$d/llvm-rc" ] && PATH="$d:$PATH"; done
export PATH="$HOME/.cargo/bin:$PATH" CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$PWD/target}"
# VIZIER_PROFILE=fast: the quick rebuild (Cargo.toml [profile.fast]); signed releases build `release`.
profile="${VIZIER_PROFILE:-release}"
cargo xwin build --profile "$profile" --locked --target x86_64-pc-windows-msvc
out="$CARGO_TARGET_DIR/x86_64-pc-windows-msvc/$profile/vizier.exe"
ls -la "$out"
