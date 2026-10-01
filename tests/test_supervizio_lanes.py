"""supervizio's packaging lanes, moved here from supervizio/runner-template with
the guard-rails its release contract tests pinned on them (AgentPackagesGuard
Rails, AgentSolarishPackaging, LibprobeSolarishGuardRails there). Both are
public workflows holding a credential to a private repository, and the first
produces release assets: what keeps a fork's code away from that credential,
and the credential away from anything that does not need it, is pinned here.

Run: python3 -m unittest discover -s tests
"""

import os
import re
import unittest

WORKFLOWS = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, ".github", "workflows")


def code(text):
    # Comments may name what the workflow refuses; only code counts.
    return "\n".join(line.split(" #", 1)[0] for line in text.splitlines() if not line.lstrip().startswith("#"))


def load(name):
    with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as fh:
        text = fh.read()
    head, _, body = text.partition("\njobs:\n")
    jobs = dict(re.findall(r"^  ([A-Za-z0-9_-]+):\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", body, re.M | re.S))
    return text, head, jobs


class AgentPackagesGuardRails(unittest.TestCase):
    NAME = "reusable-agent-packages.yml"
    # The names agent's release job reads: a rename here ships a release
    # without that package.
    ARTIFACTS = {
        "supervizio-freebsd-pkg", "supervizio-netbsd-pkg",
        "supervizio-openbsd-amd64-pkg", "supervizio-openbsd-arm64-pkg",
        "supervizio-macos-amd64-pkg", "supervizio-macos-arm64-pkg",
        "supervizio-windows-choco",
        "supervizio-illumos-amd64-pkg", "supervizio-solaris-amd64-pkg",
    }

    @classmethod
    def setUpClass(cls):
        cls.workflow, cls.head, cls.jobs = load(cls.NAME)

    def test_only_called(self):
        on = re.search(r"^on:\n(.*?)(?=^\S)", self.head, re.M | re.S).group(1)
        self.assertEqual(set(re.findall(r"^  ([a-z_]+):", on, re.M)), {"workflow_call"})
        self.assertNotIn("pull_request", code(self.workflow))

    def test_the_token_lives_in_the_environment_only(self):
        self.assertEqual(set(re.findall(r"secrets\.([A-Za-z0-9_]+)", code(self.workflow))), {"CI_APP_PRIVATE_KEY"})
        for name, job in self.jobs.items():
            uses_token = "secrets.CI_APP_PRIVATE_KEY" in code(job)
            in_env = re.search(r"^    environment: private-source$", job, re.M) is not None
            self.assertEqual(uses_token, in_env, name)
            if uses_token:
                self.assertIn("persist-credentials: false", job, name)
                self.assertIn("owner: supervizio\n", job, name)
                self.assertIn("repositories: agent\n", job, name)
                self.assertEqual(set(re.findall(r"permission-([a-z-]+): (\w+)", job)),
                                 {("contents", "read"), ("actions", "read")}, name)

    def test_admit_holds_nothing_and_gates_everything(self):
        admit = self.jobs["admit"]
        self.assertNotIn("secrets.", code(admit))
        self.assertNotIn("environment:", admit)
        for name, job in self.jobs.items():
            if name != "admit":
                self.assertIsNotNone(re.search(r"^    needs: \[admit\]$", job, re.M), name)

    def test_every_private_read_uses_admitted_values(self):
        # The SHA and the run id a token job reads come from admit's outputs,
        # never from the payload.
        for name, job in self.jobs.items():
            if name == "admit":
                continue
            self.assertIn("SOURCE_SHA: ${{ needs.admit.outputs.sha }}", job, name)
            self.assertIn("SOURCE_RUN_ID: ${{ needs.admit.outputs.run_id }}", job, name)

    def test_no_go_source_is_checked_out(self):
        allowed = {"/setup/", "/setup/init/windows/", "/examples/config.yaml", "/LICENSE",
                   "/e2e/test-install.ps1", "/.github/scripts/bsd-package-in-guest.sh",
                   "/.github/scripts/solarish-package-in-guest.sh"}
        for name, job in self.jobs.items():
            for block in re.findall(r"sparse-checkout: \|\n((?:\s{12}\S.*\n)+)", job):
                self.assertLessEqual({p.strip() for p in block.splitlines()}, allowed, name)
            checkouts = code(job).count("uses: actions/checkout@")
            self.assertEqual(checkouts, code(job).count("sparse-checkout-cone-mode: false"), name)

    def test_only_packages_leave(self):
        paths = re.findall(r"uses: actions/upload-artifact@.*?\n(?:\s+.*\n)*?\s+path: (\S+)", self.workflow)
        self.assertTrue(paths)
        self.assertEqual(set(paths), {"artifacts/pkg/"})

    def test_artifact_names(self):
        self.assertEqual(set(re.findall(r'"pkg_artifact":"([^"]+)"', self.workflow)), self.ARTIFACTS)


class AgentSolarishPackaging(unittest.TestCase):
    """agent's illumos and Solaris packages come from binaries agent cross-built
    on its own runner: no job here compiles, and the guest gets binaries and
    the packaging tree only."""

    @classmethod
    def setUpClass(cls):
        cls.workflow, _, cls.jobs = load(AgentPackagesGuardRails.NAME)

    def test_both_kernels_are_packaged_from_downloaded_binaries(self):
        admit = self.jobs["admit"]
        for platform, os_, release in (("illumos-amd64", "omnios", "r151054"), ("solaris-amd64", "solaris", "11.4")):
            with self.subTest(platform=platform):
                entry = re.search(r'\{"platform":"%s"[^}]*\}' % platform, admit)
                self.assertIsNotNone(entry)
                self.assertIn(f'"os":"{os_}"', entry.group(0))
                self.assertIn(f'"release":"{release}"', entry.group(0))
                self.assertIn(f'"artifact":"supervizio-{platform}"', entry.group(0))
                self.assertIn(f'"tests_artifact":"solarish-tests-{platform}"', entry.group(0))
        job = self.jobs["solarish"]
        self.assertEqual(job.count("uses: actions/download-artifact@"), 2)
        self.assertNotRegex(job, r"\bgo (build|test)\b|cargo |go\.dev/dl")

    def test_the_guests_run_agents_packaging_script_and_only_packages_leave(self):
        job = self.jobs["solarish"]
        runs = re.findall(r"^\s+run: (.+)$", job, re.M)
        guest = [r for r in runs if "solarish-package-in-guest.sh" in r]
        self.assertEqual(guest, ["sh .github/scripts/solarish-package-in-guest.sh"] * 2)
        self.assertEqual(set(re.findall(r"retention-days: (\d+)", job)), {"1"})
        self.assertEqual(set(re.findall(r"uses: actions/upload-artifact@.*?\n(?:\s+.*\n)*?\s+path: (\S+)", job)), {"artifacts/pkg/"})


class LibprobeSolarishGuardRails(unittest.TestCase):
    """libprobe's illumos/Solaris suite holds a credential to libprobe. It
    downloads a bundle of binaries and a plan, never a checkout of libprobe,
    and answers with its conclusion and a one-day report."""

    @classmethod
    def setUpClass(cls):
        cls.workflow, cls.head, cls.jobs = load("reusable-libprobe-solarish.yml")

    def test_only_called(self):
        on = re.search(r"^on:\n(.*?)(?=^\S)", self.head, re.M | re.S).group(1)
        self.assertEqual(set(re.findall(r"^  ([a-z_]+):", on, re.M)), {"workflow_call"})
        self.assertNotIn("pull_request", code(self.workflow))

    def test_the_token_lives_in_the_environment_only(self):
        self.assertEqual(set(re.findall(r"secrets\.([A-Za-z0-9_]+)", code(self.workflow))), {"CI_APP_PRIVATE_KEY"})
        for name, job in self.jobs.items():
            uses_token = "secrets.CI_APP_PRIVATE_KEY" in code(job)
            self.assertEqual(uses_token, re.search(r"^    environment: private-source$", job, re.M) is not None, name)
        # libprobe alone, and nothing but reading its artifacts.
        guest = code(self.jobs["guest"])
        self.assertIn("owner: supervizio\n", guest)
        self.assertIn("repositories: libprobe\n", guest)
        self.assertEqual(re.findall(r"permission-([a-z-]+): (\w+)", guest), [("actions", "read")])

    def test_admit_holds_nothing_and_gates_everything(self):
        self.assertNotIn("secrets.", code(self.jobs["admit"]))
        self.assertNotIn("environment:", self.jobs["admit"])
        for name, job in self.jobs.items():
            if name != "admit":
                self.assertIsNotNone(re.search(r"^    needs: \[admit\]$", job, re.M), name)

    def test_libprobe_is_never_checked_out(self):
        text = code(self.workflow)
        # The one checkout is kodflow/runner-template's, for the guest script.
        # (The pattern supervizio's copy used wanted ten spaces of indent, so
        # it captured nothing and its assertion held vacuously.)
        steps = re.findall(r"uses: actions/checkout@[^\n]*\n((?:\s{8,}\S.*\n)*)", text)
        self.assertEqual(len(steps), 1)
        for step in steps:
            self.assertIn("repository: kodflow/runner-template\n", step)
            self.assertIn("/scripts/solarish-leg/libprobe-tests.sh", step)
        self.assertEqual(text.count("repository: supervizio/libprobe"), 1)  # the download, only
        self.assertRegex(text, r"uses: actions/download-artifact@[^\n]*\n(?:\s+.*\n)*?\s+repository: supervizio/libprobe")
        self.assertNotRegex(text, r"\bcargo |rustup|go build")

    def test_the_bundle_is_checked_before_a_guest_boots(self):
        job = code(self.jobs["guest"])
        check = job.index("unexpected files in the bundle")
        self.assertLess(check, job.index("uses: vmactions/omnios-vm@"))
        self.assertLess(check, job.index("uses: vmactions/solaris-vm@"))

    def test_only_the_report_leaves_for_one_day(self):
        paths = re.findall(r"uses: actions/upload-artifact@.*?\n(?:\s+.*\n)*?\s+path: (\S+)", self.workflow)
        self.assertEqual(paths, ["libprobe-out/"])
        self.assertEqual(set(re.findall(r"retention-days: (\d+)", self.workflow)), {"1"})


if __name__ == "__main__":
    unittest.main()
