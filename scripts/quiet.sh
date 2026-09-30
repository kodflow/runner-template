#!/usr/bin/env bash
# quiet.sh <label> <command>...
#
# Runs a build command with its output withheld. A compiler, npm or vite
# error quotes the source it failed on, and this repository's logs are
# public: the log goes to a file under RUNNER_TEMP, which the ephemeral
# runner discards and nothing uploads. Only the label and the verdict print.
#
# Reproduce a red build from the private repository itself, where the log
# may be read.
set -euo pipefail

label=$1
shift

log="${RUNNER_TEMP:-/tmp}/build-$$.log"
start=$SECONDS
if "$@" >"$log" 2>&1; then
  echo "ok   ${label} ($((SECONDS - start))s)"
  rm -f "$log"
else
  code=$?
  rm -f "$log"
  echo "::error::${label} failed with exit code ${code} (log withheld: this repository is public; reproduce from the private repository)"
  exit "$code"
fi
