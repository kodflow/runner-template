#!/usr/bin/env bash
# fetch-source.sh <owner/repo> <sha> <dest>
#
# Shallow fetch of one private commit, silently. Not actions/checkout: its log
# is public here, and a detached checkout prints the commit subject. This
# prints nothing about the tree, only whether the fetch worked.
#
# The token travels as a one-shot `-c http.extraheader`, so it is never
# written to .git/config, and its base64 form is masked before use: GitHub
# masks the secret itself, not encodings of it.
#
# Environment: SOURCE_TOKEN (Contents: read on the repository).
set -euo pipefail

repo=$1
sha=$2
dest=$3

: "${SOURCE_TOKEN:?}"

basic=$(printf 'x-access-token:%s' "$SOURCE_TOKEN" | base64 | tr -d '\n')
echo "::add-mask::${basic}"

mkdir -p "$dest"
git -C "$dest" init -q
if ! git -C "$dest" -c "http.extraheader=AUTHORIZATION: basic ${basic}" \
  fetch -q --depth=1 --no-tags "https://github.com/${repo}.git" "$sha" >/dev/null 2>&1; then
  echo "::error::could not fetch ${repo}@${sha} (token scope, or a commit that does not exist)"
  exit 1
fi
git -C "$dest" -c advice.detachedHead=false checkout -q FETCH_HEAD >/dev/null 2>&1
echo "fetched ${repo}@${sha}"
