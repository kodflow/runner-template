#!/usr/bin/env bash
# build-ssm-gui.sh <source dir> <output dir>
#
# kodflow/ssm-gui as a universal .app, built by wails itself on macOS — the
# bundle the osxcross job could not make (it produced a bare binary). The
# .app is zipped with ditto, which keeps the bundle's symlinks and extended
# attributes where `zip` would not. Unsigned, as the original build was.
#
# Environment: WAILS_VERSION (the wails CLI version pinned by the workflow).
set -euo pipefail

src=$(cd "$1" && pwd)
mkdir -p "$2"
out=$(cd "$2" && pwd)
here=$(cd "$(dirname "$0")" && pwd)

: "${WAILS_VERSION:?}"

# Go 1.21's internal linker writes no LC_UUID, and macOS 26's dyld refuses
# to load a binary without one ("missing LC_UUID load command"). Linking the
# CLI through Apple's ld adds it; the ad-hoc signature is then redone because
# cmd/go rewrites the build ID after ld signed the file, and an arm64 binary
# with a stale signature is killed on exec. The app itself uses CGO, so it is
# externally linked already and carries its LC_UUID.
wails="$(go env GOPATH)/bin/wails"
"$here/quiet.sh" "install wails ${WAILS_VERSION}" \
  go install -ldflags=-linkmode=external "github.com/wailsapp/wails/v2/cmd/wails@${WAILS_VERSION}"
"$here/quiet.sh" "re-sign the wails CLI" codesign -s - -f "$wails"

# -skipbindings: generating them builds and runs a throwaway pure-Go binary,
# which hits the same LC_UUID refusal. ssm-gui commits frontend/wailsjs, so
# the build uses the bindings in the tree; regenerate them there on change.
cd "$src"
"$here/quiet.sh" "wails build darwin/universal" \
  "$wails" build -platform darwin/universal -skipbindings -o ssm-gui

shopt -s nullglob
apps=(build/bin/*.app)
[ "${#apps[@]}" -eq 1 ] || { echo "::error::expected one .app in build/bin, found ${#apps[@]}"; exit 1; }

exe=$(find "${apps[0]}/Contents/MacOS" -type f -perm -u+x | head -n 1)
archs=$(lipo -archs "$exe")
for want in x86_64 arm64; do
  case " $archs " in
    *" $want "*) ;;
    *) echo "::error::the app executable is '$archs', not universal (no $want)"; exit 1 ;;
  esac
done

ditto -c -k --keepParent "${apps[0]}" "$out/ssm-gui-darwin-universal.app.zip"
echo "packed ssm-gui-darwin-universal.app.zip ($archs)"
