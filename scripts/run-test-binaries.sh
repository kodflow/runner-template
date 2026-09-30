#!/usr/bin/env bash
# run-test-binaries.sh <binary dir> <report dir> <spec>...
#
# Executes Go test binaries compiled elsewhere (`go test -c`). A spec is
# `<binary>` or `<binary>:<-test.run regexp>`.
#
# Each binary runs from an empty scratch directory, not from the package
# directory `go test` would use: no source tree exists on this runner, and
# none may. Its output goes to <report dir>/<binary>.txt, never to this
# public log: a failure message can quote whatever the test compared. The
# log says ok or FAIL per binary; the reports travel as an artifact the
# calling repository downloads, prints in its own private log, and deletes
# with this run.
set -euo pipefail

dir=$1
reports=$2
shift 2

[ -d "$dir" ] || { echo "::error::no binary directory in the artifact (layout changed?)"; exit 1; }
mkdir -p "$reports"

failed=0
for spec in "$@"; do
  bin=${spec%%:*}
  run=
  [ "$spec" != "$bin" ] && run=${spec#*:}

  path="$dir/$bin"
  report="$reports/${bin}.txt"
  [ -f "$path" ] || { echo "::error::missing test binary $bin in the artifact"; echo "missing from the artifact" >"$report"; failed=1; continue; }
  # Artifacts are zip archives: the executable bit does not survive them.
  chmod +x "$path"

  work=$(mktemp -d)
  args=(-test.count=1 -test.timeout=10m)
  [ -n "$run" ] && args+=("-test.run=$run")

  start=$SECONDS
  if (cd "$work" && "$path" "${args[@]}") >"$report" 2>&1; then
    echo "ok   ${bin}${run:+ -test.run=$run} ($((SECONDS - start))s)"
  else
    echo "::error::FAIL ${bin}${run:+ -test.run=$run} ($((SECONDS - start))s): details in the report artifact, read by the calling repository"
    failed=1
  fi
  rm -rf "$work"
done

exit "$failed"
