# runner-template

The **one source** of the public companion CI of every private repository of
kodflow, supervizio, kitsunium and kodmain. It runs, on GitHub-hosted
**macOS**, **Windows**, **BSD** and **illumos/Solaris** machines, what those
repositories cannot do on their self-hosted Linux runners, and it runs it for
free: a public repository's hosted minutes cost nothing.

## Reusable workflows: the logic here, a stub per owner

The logic lives in this repository only, as reusable workflows
(`on: workflow_call`, files named `reusable-*.yml`). Each owner has its own
public `<owner>/runner-template` holding nothing but **stubs**: a few lines
`on: repository_dispatch` that call
`kodflow/runner-template/.github/workflows/reusable-<x>.yml@<full SHA>`.

A called workflow runs **in the calling repository**. So the run, its log and
its artifacts are the owner's public repository's (free, and deleted by the
private caller once it has its results); the `private-source` environment, its
`CI_APP_PRIVATE_KEY` secret and the `vars.CI_APP_CLIENT_ID` the jobs read are
the owner's; `github.*` is the caller's. Nothing of an owner is stored here.

| Reusable workflow | Stub (`<owner>/runner-template`) | Called by | What the caller takes back |
|---|---|---|---|
| [`reusable-darwin-build.yml`](.github/workflows/reusable-darwin-build.yml) | kodflow `darwin-build.yml` (`darwin-build`) | `kodflow/immobilier` release, `kodflow/ssm-gui` build | `binaries-darwin-{amd64,arm64}` / `wails-build-darwin` |
| [`reusable-native-tests.yml`](.github/workflows/reusable-native-tests.yml) | kodflow `ktn-native-tests.yml` (`ktn-native-tests`) | `kodflow/ktn-linter` CI | the verdict per leg, `ktn-native-<leg>` reports |
| [`reusable-agent-packages.yml`](.github/workflows/reusable-agent-packages.yml) | supervizio `agent-packages.yml` (`build-agent-packages`) | `supervizio/agent` `public-packages.yml` | the BSD, macOS, Chocolatey and IPS packages |
| [`reusable-libprobe-solarish.yml`](.github/workflows/reusable-libprobe-solarish.yml) | supervizio `libprobe-solarish.yml` (`run-libprobe-solarish`) | `supervizio/libprobe` `solarish-guest.yml` | the verdict, `solarish-report-*` |
| [`reusable-selftest.yml`](.github/workflows/reusable-selftest.yml) | every owner, `selftest.yml` (`runner-template-selftest`) | a person proving an owner is wired | `selftest-result` |
| [`reusable-sweep.yml`](.github/workflows/reusable-sweep.yml) | every owner, `sweep.yml` (hourly) | schedule | — |

`reusable-native-tests.yml` is the generic lane: the stub names the private
repository and the legs (runner, artifact, binaries), so any private
repository's tests or end-to-end binaries that need a native kernel run there
without a new workflow here.

**The pin.** A stub calls a full commit SHA, never a branch, and passes the
same SHA as the `ref` input: `uses:` takes no expression, and the jobs read
this repository's scripts (`scripts/`) at `ref`. A change here therefore
reaches an owner only when its stubs move to the new commit.

**Who writes the stubs.** Nobody by hand. They live in
[`kodflow/post-commit`](https://github.com/kodflow/post-commit)
(`stub/runner-template/<owner>/`), and its `enforce` workflow, which already
keeps the merge gate on every repository of the four owners with one
kodflow-ci App token per owner, compares each `<owner>/runner-template`'s
files with them every night and opens a sync pull request on drift. A new
commit here goes out with `scripts/bump-runner-template.sh <sha>` there.

**What a stub cannot carry.** `environment:` is not allowed on a job that
`uses:` a workflow, so every job that reads the key names
`environment: private-source` here, inside the reusable workflow; GitHub
resolves it in the calling repository. A called workflow also reads only the
secrets it declares, and `secrets: inherit` does not cross owners: each
reusable workflow that reads the key declares `CI_APP_PRIVATE_KEY` under
`on.workflow_call.secrets`, and each stub passes
`CI_APP_PRIVATE_KEY: ${{ secrets.CI_APP_PRIVATE_KEY }}`. At the stub's level
that value is empty (the key exists only in the environment); in a job naming
the environment GitHub uses the environment's secret instead, so only those
jobs see the key. `run-name:` and `concurrency:` of a
called workflow are ignored: the stub sets both (the private caller finds its
run by that title).

This repository's own companion workflows (`darwin-build.yml`,
`ktn-native-tests.yml`, `sweep.yml`) are kodflow's stubs once `enforce` has
synced them; until then they are the pre-centralisation copies, so the
callers keep working through the switch. [`guard.yml`](.github/workflows/guard.yml)
(actionlint, shellcheck, `tests/`) and
[`post-commit.yml`](.github/workflows/post-commit.yml) (the fleet's merge
gate) are this repository's own.

## The round trip

One job in the private repository, on `kodflow-runner`, does all of it in one
attempt — dispatch and poll share a job because a re-run of a failed job
clones the successful ones, and a separate poller would wait for a request
no one sent:

1. `repository_dispatch` with a **request id** `<run id>-<run attempt>-<label>`;
2. find the run titled `<workflow> <request id>` (its `run-name`) among that
   workflow's `repository_dispatch` runs;
3. wait for it to complete;
4. download its artifacts, print reports in its own private log, re-upload
   binaries in its own run under the names its release already uses;
5. **delete the public run** (`DELETE /repos/<owner>/runner-template/actions/runs/<id>`),
   which deletes its log and artifacts with it — red or green, whenever the
   run was found.

Nothing stays public once it has been brought back. Each owner's `sweep.yml`
catches the runs of a caller that died between steps 1 and 5 (completed for
two hours, or alive for three: cancelled, then deleted).

## Threat model

Anyone can read this repository and every owner's runner-template, their
workflow logs and, while they exist, their artifacts. Anyone can open a pull
request against them.

**Triggers.** A stub runs on `repository_dispatch` only (and `sweep.yml` on a
schedule) — never `pull_request` or `pull_request_target`. Dispatching needs
write access to the owner's runner-template. A reusable workflow here runs on
`workflow_call` only, so nothing in this repository runs it by itself.
`post-commit.yml` is the one workflow that runs on `pull_request`: the
central gate as-is, holding `contents: read` and nothing else, never in the
environment below.

**Admission.** Each lane starts with an `admit` job that holds no credential
at all (`permissions: {}`, no environment, no checkout: it fetches
[`scripts/admit.sh`](scripts/admit.sh) anonymously at the pinned commit). The
stub hands the dispatch payload over as one JSON string; `admit.sh` reads it
with jq through `env`, never through `${{ }}` inside a script, and checks each
field against an anchored pattern that no line break can pass — a full 40-hex
SHA, a numeric run id, the request id format and that it names that run, the
source repository against an allowlist (or the one repository the stub
names), a character class for a version. The jobs holding the token see only
the admitted outputs.

**Credential.** No personal token. The kodflow-ci GitHub App's key
(`secrets.CI_APP_PRIVATE_KEY`) lives in each owner runner-template's
`private-source` environment, whose deployment branch policy allows `main`
only; `vars.CI_APP_CLIENT_ID` is the owner's. A job reads the key only by
naming that environment, and a `workflow_dispatch` of another branch is
refused before it starts. Each job mints, with
`actions/create-github-app-token`, an installation token for **one named
repository** and **read** permissions only — `actions: read` to download a
private run's binaries, `contents: read` to fetch a commit, `metadata: read`
on the owner's own runner-template for `selftest` — revoked when the job ends.
Nothing writes to a private repository: the verdict is the run's conclusion,
which the caller reads. Workflows start from `permissions: {}` (or
`contents: read`) and grant per job.

**Hygiene.** No job leaves anything sensitive in public:

- no source uploaded or printed. The private commit is fetched with
  `git fetch -q` ([`scripts/fetch-source.sh`](scripts/fetch-source.sh)), not
  `actions/checkout`, whose detached checkout prints the commit subject; the
  token is a one-shot header, never written to `.git/config`, its base64 form
  masked. agent's packaging reads a sparse checkout of its packaging tree
  (`setup/`, the guest wrappers), never its Go sources;
- no build log: every build command runs through
  [`scripts/quiet.sh`](scripts/quiet.sh), which prints the step and its
  verdict only. Reproduce a red build from the private repository;
- no test output in the log: [`scripts/run-test-binaries.sh`](scripts/run-test-binaries.sh)
  prints ok/FAIL per binary and writes the output to the report artifact; the
  illumos/Solaris guests print their `=== ` stage lines only;
- no `set -x`, no listing of the private tree;
- `setup-go` and `setup-node` run with caching **off**: a cache saved by a
  `main` run is readable by any `pull_request` run of a public repository,
  and the Go build cache would hold the private code compiled;
- every artifact has `retention-days: 1`, and is deleted with its run by the
  caller long before that.

**Supply chain.** Every action is pinned to a full commit SHA, and every stub
pins this repository to one. The only code that runs next to the token is
this repository's scripts at that commit, those actions, and — for agent's
packaging only — agent's own guest wrappers read at the commit being
packaged.

## Setup

Everything runs on tokens of the **kodflow-ci GitHub App**, installed on each
of the four owners, minted per job and scoped down:

| Where | Holds | Token minted |
|---|---|---|
| `<owner>/runner-template`, environment `private-source` (main only) | secret `CI_APP_PRIVATE_KEY`; variable `CI_APP_CLIENT_ID` (environment or owner level) | one repository of that owner, read only |
| each private caller (repository or owner level, visible to private repositories) | the same secret and variable | `repositories: runner-template` + `permission-contents: write` (dispatch) + `permission-actions: write` (read the run, download, delete it) |

An installation token lives one hour: the caller mints a fresh one for each
phase (dispatch, wait, download, delete) rather than stretching one across a
queued macOS run.

`tests/test_workflows.py` (run by `guard.yml` on every push) fails if the key
is read outside the environment, if a token loses its single repository or
asks for anything but `read`, if a personal-token secret comes back, if a
workflow gains a `pull_request` trigger, if a reusable workflow takes another
trigger than `workflow_call`, reads the payload anywhere but in a
credential-free `admit` job, or checks this repository out at anything but
the pinned `ref`, or if an action, a checkout, an artifact or a setup cache
breaks the hygiene rules above. `tests/test_scripts.py` runs the scripts that
decide what reaches a public log, `admit.sh` included.

## Proving an owner is wired

```sh
owner=kitsunium req="$(date +%s)-1-selftest"
gh api -X POST "repos/$owner/runner-template/dispatches" \
  -f event_type=runner-template-selftest -f "client_payload[request_id]=$req"
# find the run titled "selftest $req", wait, download selftest-result,
# then delete the run: DELETE /repos/$owner/runner-template/actions/runs/<id>
```

A green `selftest` says the stub calls this repository at its pin, the
environment resolves in the owner's repository, the key and client id are
there and the App is installed on the owner. Delete the run once read:
`sweep.yml` would, two hours later.
