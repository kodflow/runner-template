# runner-template

Public companion CI for private kodflow repositories, on the model of
[`supervizio/runner-template`](https://github.com/supervizio/runner-template).
It runs, on GitHub-hosted **macOS** and **Windows** runners, what those
repositories cannot do on their self-hosted Linux runners (`kodflow-runner`):

| Workflow | Called by | What it does | What the caller takes back |
|---|---|---|---|
| [`ktn-native-tests.yml`](.github/workflows/ktn-native-tests.yml) | `kodflow/ktn-linter` CI | Downloads the `go test -c` binaries ktn-linter cross-compiled and runs them on `macos-latest` (arm64), `macos-15-intel` and `windows-latest` | The verdict of each leg, and its report artifact `ktn-native-<leg>` |
| [`darwin-build.yml`](.github/workflows/darwin-build.yml) | `kodflow/immobilier` release, `kodflow/ssm-gui` build | Fetches the private commit and builds it natively on `macos-latest`: immobilier's scrapers (CGO/SQLite, amd64 + arm64), ssm-gui's universal `.app` via wails | Artifacts `binaries-darwin-{amd64,arm64}` / `wails-build-darwin` |
| [`sweep.yml`](.github/workflows/sweep.yml) | schedule | Deletes dispatched runs no caller took back | — |
| [`guard.yml`](.github/workflows/guard.yml) | push | actionlint, shellcheck and `tests/test_workflows.py` | — |
| [`post-commit.yml`](.github/workflows/post-commit.yml) | pull requests, pushes | The fleet's merge gate, the central stub of [`kodflow/post-commit`](https://github.com/kodflow/post-commit) | — |

Hosted runners are free for a public repository. That is the only reason this
repository is public, and everything below follows from it.

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
5. **delete the public run** (`DELETE /repos/kodflow/runner-template/actions/runs/<id>`),
   which deletes its log and artifacts with it — red or green, whenever the
   run was found.

Nothing stays public once it has been brought back. `sweep.yml` catches the
runs of a caller that died between steps 1 and 5.

## Threat model

Anyone can read this repository, its workflow logs and, while they exist, its
artifacts. Anyone can open a pull request against it.

**Triggers.** The companion workflows run on `repository_dispatch` and
`workflow_dispatch` only — never `pull_request` or `pull_request_target`.
Both need write access to this repository. `post-commit.yml` is the one
workflow here that runs on `pull_request`: the central stub as-is, holding
`contents: read` on this repository and nothing else, never in the
environment below.

**Admission.** Each companion workflow starts with an `admit` job that holds
no credential. It checks the payload — the source repository against a
hard-coded allowlist, a full 40-hex SHA, a numeric run id, the request id
format, a character class for the version — reading it through `env`, never
through `${{ }}` inside a script. The jobs holding the token see only the
admitted outputs.

**Credential.** No personal token. The kodflow-ci GitHub App's key
(`secrets.CI_APP_PRIVATE_KEY`, with `vars.CI_APP_CLIENT_ID`) lives in the
`private-source` environment, whose deployment branch policy allows `main`
only: a job reads it only by naming that environment, and a
`workflow_dispatch` of another branch is refused before it starts. Each job
mints, with `actions/create-github-app-token`, an installation token for **one
named private repository** and **read** permissions only (`actions: read` on
ktn-linter to download the test binaries, `contents: read` on immobilier or
ssm-gui to fetch the commit), revoked when the job ends. Nothing here writes to
a private repository: the verdict is the run's conclusion, which the caller
reads. Workflows start from `permissions: {}` and grant `contents: read` per
job, to check out this repository with `persist-credentials: false`.

**Hygiene.** No job leaves anything sensitive in public:

- no source uploaded or printed. The private commit is fetched with
  `git fetch -q` ([`scripts/fetch-source.sh`](scripts/fetch-source.sh)), not
  `actions/checkout`, whose detached checkout prints the commit subject; the
  token is a one-shot header, never written to `.git/config`, its base64 form
  masked;
- no build log: every build command runs through
  [`scripts/quiet.sh`](scripts/quiet.sh), which prints the step and its
  verdict only. Reproduce a red build from the private repository;
- no test output in the log: [`scripts/run-test-binaries.sh`](scripts/run-test-binaries.sh)
  prints ok/FAIL per binary and writes the output to the report artifact;
- no `set -x`, no listing of the private tree;
- `setup-go` and `setup-node` run with caching **off**: a cache saved by a
  `main` run is readable by any `pull_request` run of a public repository,
  and the Go build cache would hold the private code compiled;
- every artifact has `retention-days: 1`, and is deleted with its run by the
  caller long before that.

**Supply chain.** Every action is pinned to a full commit SHA. The only code
that runs next to the token is this repository's scripts and those actions —
never a script from the private checkout.

## Setup

Everything runs on tokens of the **kodflow-ci GitHub App** (installed on the
whole kodflow account), minted per job and scoped down:

| Where | Holds | Token minted |
|---|---|---|
| this repository, environment `private-source` (main only) | secret `CI_APP_PRIVATE_KEY`, variable `CI_APP_CLIENT_ID` | `repositories: ktn-linter` + `permission-actions: read`; or `immobilier` / `ssm-gui` + `permission-contents: read` |
| `kodflow/ktn-linter`, `kodflow/immobilier`, `kodflow/ssm-gui` (repository level) | the same secret and variable | `repositories: runner-template` + `permission-contents: write` (dispatch) + `permission-actions: write` (read the run, download, delete it) |

An installation token lives one hour: the caller mints a fresh one for each
phase (dispatch, wait, download, delete) rather than stretching one across a
queued macOS run.

`tests/test_workflows.py` (run by `guard.yml` on every push) fails if the key
is read outside the environment, if a token loses its single repository or
asks for anything but `read` here, if a personal-token secret comes back, if
a companion workflow gains a `pull_request` trigger, or if an action, a
checkout, an artifact or a setup cache breaks the hygiene rules above.

## Running by hand

*Actions → ktn-native-tests* or *darwin-build* → *Run workflow*. No caller
looks for a manual run, so nothing downloads or deletes it and `sweep.yml`
leaves it alone: delete it yourself once read.
