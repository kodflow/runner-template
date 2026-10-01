"""The rules of a public repository holding a credential to private ones,
checked on the workflow files rather than trusted to review.

Run: python3 -m unittest discover -s tests
"""

import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")
SCRIPTS = os.path.join(ROOT, "scripts")

# The fleet's merge gate: the central stub as-is, the one workflow allowed to
# run on pull_request, and to use an unpinned action (kodflow/post-commit@main,
# on purpose: see its header).
GATE = "post-commit.yml"
APP_TOKEN_PIN = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"
# Each token names its repository literally, or takes it from the stub (an
# input the stub sets, split by a credential-free admit job), or is the
# caller's own runner-template (selftest).
PRIVATE_REPOS = (
    "ktn-linter", "immobilier", "ssm-gui", "agent", "libprobe", "runner-template",
    "${{ needs.admit.outputs.name }}",
)
OWNERS = ("kodflow", "supervizio", "${{ needs.admit.outputs.owner }}", "${{ github.repository_owner }}")
# The logic every owner's runner-template stub calls: `on: workflow_call` only.
REUSABLE_PREFIX = "reusable-"
RETIRED = ("PRIVATE_SOURCE_TOKEN", "RUNNER_TEMPLATE_DISPATCH_TOKEN", "NATIVE_CI_SOURCE_TOKEN")


def code(text):
    """The text without comments, so a comment naming a rule never satisfies it."""
    return "\n".join(
        line.split(" #", 1)[0] for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def secret_refs(text, name):
    """Every reference to a secret, dotted or indexed (secrets['NAME'])."""
    return re.findall(r"secrets(?:\.%s\b|\[\s*['\"]%s['\"]\s*\])" % (name, name), code(text))


def workflows():
    for name in sorted(os.listdir(WORKFLOWS)):
        if name.endswith((".yml", ".yaml")):
            with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as fh:
                yield name, fh.read()


def jobs(text):
    body = code(text).partition("\njobs:\n")[2]
    return dict(re.findall(r"^  ([A-Za-z0-9_-]+):\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", body, re.M | re.S))


def on_block(text):
    m = re.search(r"^on:\n((?:[ #].*\n|\n)*)", code(text) + "\n", re.M)
    return m.group(1) if m else ""


class Triggers(unittest.TestCase):
    def test_no_pull_request_trigger_outside_the_gate(self):
        for name, text in workflows():
            if name == GATE:
                continue
            triggers = on_block(text)
            self.assertTrue(triggers, f"{name}: no on: block found")
            self.assertNotRegex(triggers, r"(?m)^\s+pull_request_target\b", name)
            self.assertNotRegex(triggers, r"(?m)^\s+pull_request\b", name)
            self.assertNotRegex(triggers, r"(?m)^\s+workflow_run", name)
            # The inline forms (`on: pull_request`, `on: [push, pull_request]`)
            # would escape the block check above: only the block form passes.
            self.assertNotRegex(code(text), r"(?m)^on:[ \t]*[^\s#]", name)

    def test_companions_are_dispatch_only(self):
        for name in ("ktn-native-tests.yml", "darwin-build.yml"):
            with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as fh:
                keys = set(re.findall(r"^  ([a-z_]+):", on_block(fh.read()), re.M))
            self.assertEqual(keys, {"repository_dispatch", "workflow_dispatch"}, name)


class Reusable(unittest.TestCase):
    """The workflows every owner's stub calls. A called workflow runs in the
    caller: its log, artifacts, environment, vars and secrets are the
    caller's. What makes that safe is checked here."""

    def reusables(self):
        found = [(n, t) for n, t in workflows() if n.startswith(REUSABLE_PREFIX)]
        self.assertGreaterEqual(len(found), 6)
        return found

    def test_called_only(self):
        for name, text in self.reusables():
            keys = set(re.findall(r"^  ([a-z_]+):", on_block(text), re.M))
            self.assertEqual(keys, {"workflow_call"}, name)

    def test_no_run_name_or_concurrency(self):
        # Both are ignored in a called workflow (the stub's apply): one here
        # would only mislead the reader about what names the run.
        for name, text in self.reusables():
            self.assertNotRegex(code(text), r"(?m)^(run-name|concurrency):", name)

    def test_lanes_take_the_pin_and_the_payload(self):
        for name, text in self.reusables():
            if name == REUSABLE_PREFIX + "sweep.yml":
                continue
            inputs = on_block(text)
            for key in ("ref", "payload"):
                self.assertRegex(inputs, r"(?m)^      %s:\n" % key, f"{name}: input {key}")
            # The payload is read through env by scripts/admit.sh, never
            # pasted into a script by the template engine.
            self.assertFalse("client_payload" in code(text), name)
            self.assertEqual(code(text).count("${{ inputs.payload }}"), 1, name)

    def test_admit_holds_nothing(self):
        for name, text in self.reusables():
            if name == REUSABLE_PREFIX + "sweep.yml":
                continue
            job = jobs(text).get("admit")
            self.assertIsNotNone(job, f"{name}: no admit job")
            self.assertRegex(job, r"(?m)^    permissions: \{\}$", name)
            self.assertNotIn("environment:", job, name)
            self.assertNotIn("secrets.", job, name)
            self.assertNotIn("uses: actions/checkout", job, name)
            self.assertIn("scripts/admit.sh", job, name)
            self.assertIn("PAYLOAD: ${{ inputs.payload }}", job, name)
            # Every other job waits for it.
            for other, body in jobs(text).items():
                if other != "admit":
                    self.assertRegex(body, r"(?m)^    needs: \[?admit\]?$", f"{name}:{other}")

    def test_runner_template_is_checked_out_at_the_pin(self):
        for name, text in self.reusables():
            for block in re.findall(r"uses: actions/checkout@.*?\n((?:\s{8,}\S.*\n)*)", text):
                if "repository: kodflow/runner-template" in block:
                    self.assertIn("ref: ${{ inputs.ref }}", block, name)


class AppKeyStaysInTheEnvironment(unittest.TestCase):
    """The kodflow-ci App's key is the only credential this repository holds on
    the private ones. Only jobs of the `private-source` environment (main
    only) read it, each mints a read-only token for one named repository, and
    no personal-token secret comes back."""

    def test_no_personal_token_secret(self):
        for name, text in workflows():
            for secret in RETIRED:
                self.assertEqual(secret_refs(text, secret), [], name)

    def test_the_key_is_read_only_in_the_environment(self):
        seen = 0
        for wf, text in workflows():
            for name, job in jobs(text).items():
                if not secret_refs(job, "CI_APP_PRIVATE_KEY"):
                    continue
                seen += 1
                envs = re.findall(r"^    environment: .*$", job, re.M)
                self.assertEqual([e.strip() for e in envs], ["environment: private-source"], f"{wf}:{name}")
        self.assertGreaterEqual(seen, 3)

    def test_every_token_is_scoped_and_read_only(self):
        steps = 0
        for wf, text in workflows():
            for block in re.findall(r"uses: actions/create-github-app-token@.*?\n((?:\s{8,}\S.*\n)+)", text):
                steps += 1
                owners = re.findall(r"owner: (.+)\n", block)
                self.assertEqual(len(owners), 1, wf)
                self.assertIn(owners[0].strip(), OWNERS, wf)
                self.assertIn("client-id: ${{ vars.CI_APP_CLIENT_ID }}", block, wf)
                self.assertNotIn("app-id:", block, wf)
                repos = re.findall(r"repositories: (.+)\n", block)
                repos = [r.strip() for r in repos]
                self.assertEqual(len(repos), 1, wf)
                self.assertIn(repos[0], PRIVATE_REPOS, wf)
                perms = re.findall(r"permission-([a-z-]+): (\S+)", block)
                self.assertTrue(perms, wf)
                for perm, level in perms:
                    # Nothing here writes to a private repository: the verdict
                    # is the run's conclusion, which the caller reads.
                    self.assertEqual(level, "read", f"{wf}: permission-{perm}")
            self.assertEqual(text.count("uses: actions/create-github-app-token@"), text.count(APP_TOKEN_PIN), wf)
            outside = code(text).replace("private-key: ${{ secrets.CI_APP_PRIVATE_KEY }}", "")
            self.assertEqual(secret_refs(outside, "CI_APP_PRIVATE_KEY"), [], wf)
        self.assertGreaterEqual(steps, 3)


class Hygiene(unittest.TestCase):
    def test_actions_are_pinned_by_sha(self):
        for name, text in workflows():
            for ref in re.findall(r"uses: (\S+)", code(text)):
                if name == GATE and ref == "kodflow/post-commit@main":
                    continue
                self.assertRegex(ref, r"@[0-9a-f]{40}$", f"{name}: {ref}")

    def test_top_level_permissions_are_explicit(self):
        for name, text in workflows():
            self.assertRegex(code(text), r"(?m)^permissions:", name)

    def test_checkout_never_persists_credentials(self):
        for name, text in workflows():
            for block in re.findall(r"uses: actions/checkout@.*?\n((?:\s{8,}\S.*\n)*)", text):
                self.assertIn("persist-credentials: false", block, name)

    def test_artifacts_live_one_day(self):
        for name, text in workflows():
            blocks = re.findall(r"uses: actions/upload-artifact@.*?\n((?:\s{8,}\S.*\n)*)", text)
            for block in blocks:
                self.assertIn("retention-days: 1\n", block, name)

    def test_no_xtrace(self):
        texts = list(workflows())
        for base, _, names in os.walk(SCRIPTS):
            for name in names:
                with open(os.path.join(base, name), encoding="utf-8") as fh:
                    texts.append((name, fh.read()))
        for name, text in texts:
            self.assertNotRegex(code(text), r"set -[a-wyz]*x|set -o xtrace|bash -x", name)

    def test_setup_caches_are_off(self):
        for name, text in workflows():
            for block in re.findall(r"uses: actions/setup-go@.*?\n((?:\s{8,}\S.*\n)*)", text):
                self.assertIn("cache: false", block, name)
            for block in re.findall(r"uses: actions/setup-node@.*?\n((?:\s{8,}\S.*\n)*)", text):
                self.assertIn("package-manager-cache: false", block, name)


if __name__ == "__main__":
    unittest.main()
