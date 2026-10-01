#!/bin/sh
# Runs INSIDE the OmniOS or Solaris guest of libprobe-solarish.yml: executes
# the test bundle libprobe cross-built on its own runner
# (libprobe .github/scripts/solarish-cross-tests.sh) -- binaries and a plan,
# no source -- and writes a verdict.
#
# Usage: libprobe-tests.sh <bundle dir> <report dir>
#
# The plan (<bundle>/plan.txt), one stage a line, fields separated by spaces:
#   run    <stage> <binary> [args...]             exit 0 required
#   report <stage> <binary> [args...]             exit 0 required; stdout kept
#                                                 as <stage>.json
#   abi    <stage> <checker> <binary> [args...]   `<binary> [args...]`'s
#                                                 output is fed to <checker>,
#                                                 which must exit 0
# Binaries are paths relative to the bundle.
#
# Every stage's output goes to <report>/<stage>.log, which the workflow
# uploads as a one-day artifact for libprobe to download before it deletes the
# run. The console gets only `=== ` lines: stage, ok or FAILED, and the
# verdict, written to <report>/status (0 = every stage passed). Always exits
# 0: the action copies results back only after a successful command.
#
# POSIX sh: the guest's /bin/sh is ksh93 (no pipefail), so no stage's status
# is ever a pipe's.
set -u

bundle="${1:?bundle dir}"
report="${2:?report dir}"
mkdir -p "$report"
bundle="$(cd "$bundle" && pwd)"
report="$(cd "$report" && pwd)"
status=0

{ uname -a; head -1 /etc/release; } > "$report/uname.txt" 2>&1
echo "=== system: $(head -1 /etc/release | sed 's/^ *//')"
# Solaris's SVR4 tools lack what some tests shell out to; GNU's come first.
PATH="/usr/gnu/bin:/usr/bin:/usr/sbin:/sbin:$PATH"
export PATH
# The tests say why they failed, not where: a backtrace names source paths.
RUST_BACKTRACE=0
export RUST_BACKTRACE

[ -s "$bundle/plan.txt" ] || { echo "=== FAILED: no plan.txt in the bundle"; echo 1 > "$report/status"; exit 0; }
chmod +x "$bundle"/bin/* 2>/dev/null || true

n=0
# Every command reads /dev/null, never the plan this loop is reading.
# Each stage runs from the bundle, where the plan's relative paths resolve.
cd "$bundle" || exit 0
# The plan's words are split, never globbed.
set -f
while read -r kind stage bin rest; do
  case "$kind" in ''|'#'*) continue ;; esac
  n=$((n + 1))
  case "$stage" in *[!A-Za-z0-9_.-]*|'') echo "=== FAILED: bad stage name on line $n"; status=1; continue ;; esac
  log="$report/$stage.log"
  rc=0
  case "$kind" in
    run)
      # shellcheck disable=SC2086 # the plan's args are words by design
      "./$bin" $rest < /dev/null > "$log" 2>&1 || rc=$?
      ;;
    report)
      # shellcheck disable=SC2086
      "./$bin" $rest < /dev/null > "$report/$stage.json" 2> "$log" || rc=$?
      ;;
    abi)
      checker="$bin"
      # shellcheck disable=SC2086
      set -- $rest
      dump="$1"
      shift
      "./$dump" "$@" < /dev/null > "$report/$stage.dump" 2>&1 || rc=$?
      if [ "$rc" -eq 0 ]; then
        "./$checker" < "$report/$stage.dump" > "$log" 2>&1 || rc=$?
      else
        cp "$report/$stage.dump" "$log"
      fi
      ;;
    *)
      echo "=== FAILED: unknown stage kind '$kind' on line $n"
      status=1
      continue
      ;;
  esac
  if [ "$rc" -eq 0 ]; then
    echo "=== $stage: ok"
  else
    echo "=== $stage: FAILED (exit $rc)"
    status=1
  fi
done < "$bundle/plan.txt"

[ "$n" -gt 0 ] || { echo "=== FAILED: the plan holds no stage"; status=1; }
echo "$status" > "$report/status"
echo "=== verdict: $status"
exit 0
