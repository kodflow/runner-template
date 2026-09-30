#!/usr/bin/env bash
# build-immobilier.sh <source dir> <version> <output dir>
#
# The darwin half of kodflow/immobilier's release matrix, natively on macOS:
# every cmd/* scraper for amd64 and arm64, bienici with CGO (SQLite), the
# others without, then one immobilier_darwin_<arch>.tar.gz per architecture —
# the names and layout the private release job already publishes.
#
# One arm64 runner builds both architectures: Apple's clang targets x86_64
# with `-arch x86_64`, so CGO needs no Intel machine and no SDK of our own.
set -euo pipefail

src=$(cd "$1" && pwd)
version=$2
mkdir -p "$3"
out=$(cd "$3" && pwd)
here=$(cd "$(dirname "$0")" && pwd)

ldflags="-s -w -X main.version=${version}"

cd "$src"
for arch in amd64 arm64; do
  case "$arch" in
    amd64) clang_arch=x86_64 ;;
    arm64) clang_arch=arm64 ;;
  esac
  stage="$out/stage-${arch}"
  mkdir -p "$stage"

  for cmd in cmd/*/; do
    name=$(basename "$cmd")
    cgo=0
    [ "$name" = bienici ] && cgo=1
    GOOS=darwin GOARCH="$arch" CGO_ENABLED="$cgo" CC="clang -arch ${clang_arch}" \
      "$here/quiet.sh" "build ${name} darwin/${arch} (cgo=${cgo})" \
      go build -ldflags "$ldflags" -o "$stage/${name}_darwin_${arch}" "./$cmd"
  done

  # The architecture is checked, not assumed: a CC that silently ignored
  # -arch would ship arm64 code under the amd64 name.
  for bin in "$stage"/*; do
    got=$(lipo -archs "$bin")
    [ "$got" = "$clang_arch" ] || { echo "::error::$(basename "$bin") is '$got', expected $clang_arch"; exit 1; }
  done

  (cd "$stage" && tar -czf "$out/immobilier_darwin_${arch}.tar.gz" -- *)
  rm -rf "$stage"
  echo "packed immobilier_darwin_${arch}.tar.gz"
done
