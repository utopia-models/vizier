#!/usr/bin/env bash
# setup-xwin.sh: the Windows cross-build toolchain on Debian/Ubuntu (CI runs it in a rust:1 container).
# Installs clang/lld/llvm (+ osslsigncode for signing), the MSVC Rust target, and cargo-xwin. Idempotent.
# cargo-xwin needs clang-cl, lld-link and llvm-lib; tauri-winres needs llvm-rc. Distros ship some of these only as
# versioned names (clang-cl-14, /usr/lib/llvm-14/bin/...), so missing plain names are linked into /usr/local/bin.
set -euo pipefail
SUDO=; [ "$(id -u)" = 0 ] || SUDO=sudo
$SUDO apt-get update -qq
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  clang lld llvm osslsigncode ca-certificates curl git >/dev/null
rustup target add x86_64-pc-windows-msvc
command -v cargo-xwin >/dev/null || cargo install cargo-xwin --locked
find_tool() {   # plain name, else the newest versioned name, else a multicall fallback
  local n=$1 c
  command -v "$n" && return
  c=$(ls /usr/bin/"$n"-[0-9]* /usr/lib/llvm-*/bin/"$n" 2>/dev/null | sort -V | tail -1)
  [ -n "$c" ] && { echo "$c"; return; }
  case $n in   # LLVM multicall binaries dispatch on argv[0]
    clang-cl) c=$(command -v clang || true) ;;
    lld-link) c=$(command -v lld || command -v ld.lld || true) ;;
    llvm-lib) c=$(command -v llvm-ar || ls /usr/bin/llvm-ar-[0-9]* 2>/dev/null | sort -V | tail -1 || true) ;;
  esac
  [ -n "$c" ] && echo "$c"
}
missing=0
for n in clang-cl lld-link llvm-lib llvm-rc; do
  p=$(find_tool "$n" || true)
  if [ -z "$p" ]; then echo "setup-xwin: MISSING $n" >&2; missing=1; continue; fi
  [ "$p" = "/usr/local/bin/$n" ] || [ "$(command -v "$n" || true)" = "$p" ] || $SUDO ln -sfn "$p" "/usr/local/bin/$n"
  echo "setup-xwin: $n -> $p"
done
exit $missing
