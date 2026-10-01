#!/usr/bin/env bash
# admit.sh [--allow FIELD=v1,v2,...] [--run-starts-request] SPEC...
#
# The one admission check every reusable workflow here runs first, in a job
# that holds no credential. The dispatch payload is untrusted text from
# whoever holds a dispatch token: it arrives as JSON in $PAYLOAD (the stub
# passes `toJSON(github.event.client_payload)`), is read with jq, never
# pasted into a script by the template engine, and only what passes leaves
# this job, as step outputs.
#
# A SPEC is `field=regex` (required) or `field?=regex` (optional, empty when
# absent; an empty value must still match, so write the regex to allow it).
# The regex is a bash ERE matched against the whole value: write it with no
# `.` and no negated class, so that no value carrying a newline can ever
# match — a newline in $GITHUB_OUTPUT would let a payload set outputs of its
# own. That is also checked here, independently of the regex.
#
#   --allow FIELD=a,b       the field must be one of the listed values
#   --run-starts-request    request_id must start with `<run_id>-`: the
#                           request id names the private run it serves, and
#                           one whose id and run disagree is refused rather
#                           than attributed to either
#
# Fields the payload carries and no SPEC names are ignored. Output: one
# `field=value` line per SPEC in $GITHUB_OUTPUT (stdout when unset), and on
# stdout one line saying what was admitted, field names and values that
# passed the checks only.
set -euo pipefail

: "${PAYLOAD:?no PAYLOAD}"
out="${GITHUB_OUTPUT:-/dev/stdout}"

allows=()
run_starts_request=false
specs=()
while [ $# -gt 0 ]; do
  case "$1" in
    --allow) allows+=("$2"); shift ;;
    --run-starts-request) run_starts_request=true ;;
    -*) echo "::error::admit.sh: unknown option $1"; exit 2 ;;
    *) specs+=("$1") ;;
  esac
  shift
done
[ "${#specs[@]}" -gt 0 ] || { echo "::error::admit.sh: no field to admit"; exit 2; }

# A payload that is not a JSON object is refused before any field is read.
jq -e 'type == "object"' <<<"$PAYLOAD" >/dev/null 2>&1 \
  || { echo "::error::the dispatch payload is not a JSON object"; exit 1; }

declare -A got=()
names=()
for spec in "${specs[@]}"; do
  key=${spec%%=*}
  re=${spec#*=}
  optional=false
  case "$key" in *\?) optional=true; key=${key%\?} ;; esac
  [[ "$key" =~ ^[a-z][a-z0-9_]{0,31}$ ]] || { echo "::error::admit.sh: bad field name '$key'"; exit 2; }

  if jq -e --arg k "$key" 'has($k) and .[$k] != null' <<<"$PAYLOAD" >/dev/null; then
    # Strings and numbers only: an object or an array is not a value. The
    # trailing `.` keeps $(...) from stripping trailing newlines, which would
    # let a value that carries one pass as one that does not.
    value=$(jq -er --arg k "$key" '.[$k] | if type == "string" or type == "number" then tostring + "." else error("type") end' \
      <<<"$PAYLOAD" 2>/dev/null) || { echo "::error::$key must be a string"; exit 1; }
    value=${value%.}
  else
    $optional || { echo "::error::$key is required"; exit 1; }
    value=""
  fi
  case "$value" in *$'\n'* | *$'\r'*) echo "::error::$key carries a line break"; exit 1 ;; esac
  [[ "$value" =~ ^($re)$ ]] || { echo "::error::$key does not match ^($re)\$"; exit 1; }
  got[$key]=$value
  names+=("$key")
done

for a in ${allows[@]+"${allows[@]}"}; do
  key=${a%%=*}
  list=",${a#*=},"
  [ -n "${got[$key]+x}" ] || { echo "::error::admit.sh: --allow names $key, which no SPEC admits"; exit 2; }
  case "$list" in *",${got[$key]},"*) ;; *) echo "::error::refusing $key '${got[$key]}'"; exit 1 ;; esac
done

if $run_starts_request; then
  [ -n "${got[request_id]:-}" ] && [ -n "${got[run_id]:-}" ] \
    || { echo "::error::--run-starts-request needs request_id and run_id"; exit 2; }
  [ "${got[request_id]%%-*}" = "${got[run_id]}" ] || { echo "::error::request_id does not start with run_id"; exit 1; }
fi

summary=()
for key in "${names[@]}"; do
  printf '%s=%s\n' "$key" "${got[$key]}" >> "$out"
  summary+=("$key=${got[$key]}")
done
echo "admitted: ${summary[*]}"
