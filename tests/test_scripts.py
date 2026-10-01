"""What the scripts next to the token may print, checked by running them.

The build scripts need macOS and a private checkout, so they are proven by
the dispatched runs; these are the parts that decide what reaches a public
log, which any Linux box can exercise.

Run: python3 -m unittest discover -s tests
"""

import base64
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "scripts")
SECRET = "do-not-print-this-line"


def run(script, *args, env=None):
    full_env = dict(os.environ, **(env or {}))
    return subprocess.run(
        ["bash", os.path.join(SCRIPTS, script), *args],
        capture_output=True, text=True, env=full_env, check=False,
    )


def fake_test_binary(path, body):
    # A stand-in for a `go test -c` binary: prints its arguments and SECRET,
    # then exits with `body`'s status. Not executable, as an artifact
    # arrives.
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'#!/usr/bin/env bash\necho "args: $*"\necho "{SECRET}"\n{body}\n')
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


class Quiet(unittest.TestCase):
    def test_success_prints_the_label_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run("quiet.sh", "step", "sh", "-c", f"echo {SECRET}", env={"RUNNER_TEMP": tmp})
            self.assertEqual(r.returncode, 0)
            self.assertIn("ok   step", r.stdout)
            self.assertNotIn(SECRET, r.stdout + r.stderr)
            self.assertEqual(os.listdir(tmp), [], "the log must not outlive the step")

    def test_failure_keeps_the_code_and_withholds_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run("quiet.sh", "step", "sh", "-c", f"echo {SECRET} >&2; exit 7", env={"RUNNER_TEMP": tmp})
            self.assertEqual(r.returncode, 7)
            self.assertIn("::error::step failed with exit code 7", r.stdout)
            self.assertNotIn(SECRET, r.stdout + r.stderr)
            self.assertEqual(os.listdir(tmp), [])


class RunTestBinaries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bins = os.path.join(self.tmp.name, "bin")
        self.reports = os.path.join(self.tmp.name, "report")
        os.mkdir(self.bins)

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, name):
        with open(os.path.join(self.reports, name + ".txt"), encoding="utf-8") as fh:
            return fh.read()

    def test_green_binary(self):
        fake_test_binary(os.path.join(self.bins, "a.test"), "exit 0")
        r = run("run-test-binaries.sh", self.bins, self.reports, "a.test")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("ok   a.test", r.stdout)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertIn(SECRET, self.report("a.test"))

    def test_red_binary_fails_and_its_output_stays_in_the_report(self):
        fake_test_binary(os.path.join(self.bins, "a.test"), "exit 1")
        fake_test_binary(os.path.join(self.bins, "b.test"), "exit 0")
        r = run("run-test-binaries.sh", self.bins, self.reports, "a.test", "b.test")
        self.assertEqual(r.returncode, 1)
        self.assertIn("::error::FAIL a.test", r.stdout)
        # A red binary does not stop the others.
        self.assertIn("ok   b.test", r.stdout)
        self.assertNotIn(SECRET, r.stdout + r.stderr)
        self.assertIn(SECRET, self.report("a.test"))

    def test_run_filter_is_passed(self):
        fake_test_binary(os.path.join(self.bins, "s.test"), "exit 0")
        r = run("run-test-binaries.sh", self.bins, self.reports, "s.test:Windows")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("-test.run=Windows", self.report("s.test"))
        self.assertIn("-test.count=1", self.report("s.test"))

    def test_missing_binary_fails(self):
        r = run("run-test-binaries.sh", self.bins, self.reports, "nope.test")
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing test binary nope.test", r.stdout)

    def test_missing_directory_fails(self):
        r = run("run-test-binaries.sh", os.path.join(self.tmp.name, "absent"), self.reports, "a.test")
        self.assertNotEqual(r.returncode, 0)


class FetchSource(unittest.TestCase):
    def test_fetches_without_leaving_the_token_behind(self):
        # github.com is redirected to a local bare repository through a
        # throwaway global config, so the real script runs offline. A `git`
        # shim first on PATH records every invocation (to a file, never
        # printed) and hands over to the real git, so the header the script
        # builds for the authenticated HTTPS fetch can be checked.
        token = "tok-" + SECRET
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        header = f"http.extraheader=AUTHORIZATION: basic {basic}"
        with tempfile.TemporaryDirectory() as tmp:
            git = lambda *a, cwd=tmp: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True)
            git("init", "-q", "src")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x", cwd=os.path.join(tmp, "src"))
            sha = git("rev-parse", "HEAD", cwd=os.path.join(tmp, "src")).stdout.strip()
            git("clone", "-q", "--bare", "src", "bare.git")
            gitconfig = os.path.join(tmp, "gitconfig")
            with open(gitconfig, "w", encoding="utf-8") as fh:
                fh.write(f'[url "file://{tmp}/bare.git"]\n\tinsteadOf = https://github.com/kodflow/x.git\n')

            shim_dir = os.path.join(tmp, "shim")
            os.mkdir(shim_dir)
            calls = os.path.join(tmp, "calls")
            real_git = shutil.which("git")
            with open(os.path.join(shim_dir, "git"), "w", encoding="utf-8") as fh:
                fh.write(f'#!/usr/bin/env bash\nprintf \'%s\\0\' "$@" >> "{calls}"\nprintf \'\\n\' >> "{calls}"\nexec "{real_git}" "$@"\n')
            os.chmod(os.path.join(shim_dir, "git"), 0o755)

            dest = os.path.join(tmp, "dest")
            r = run("fetch-source.sh", "kodflow/x", sha, dest, env={
                "SOURCE_TOKEN": token,
                "GIT_CONFIG_GLOBAL": gitconfig,
                "PATH": shim_dir + os.pathsep + os.environ["PATH"],
            })
            self.assertEqual(r.returncode, 0, "fetch-source.sh failed (output withheld: it may carry the token)")
            self.assertEqual(git("rev-parse", "HEAD", cwd=dest).stdout.strip(), sha)

            # The authenticated path: the fetch carries the header, once, as a
            # one-shot -c, against the real https URL of the repository.
            with open(calls, encoding="utf-8") as fh:
                invocations = [line.split("\0")[:-1] for line in fh.read().split("\0\n") if line]
            fetches = [c for c in invocations if "fetch" in c]
            self.assertEqual(len(fetches), 1, "exactly one fetch expected")
            fetch = fetches[0]
            self.assertIn("-c", fetch)
            self.assertEqual(fetch[fetch.index("-c") + 1], header, "the fetch does not carry the expected header")
            self.assertIn("https://github.com/kodflow/x.git", fetch)
            others = [c for c in invocations if c is not fetch]
            self.assertFalse(any(basic in arg for c in others for arg in c), "the header leaked into another git call")

            # Neither the token nor its encoded form, in the log or on disk.
            # The one exception is the ::add-mask:: directive, which must come
            # before anything else is printed so the runner masks it first.
            out = (r.stdout + r.stderr).splitlines()
            self.assertTrue(out and out[0] == f"::add-mask::{basic}", "the mask directive must be the first line")
            for line in out[1:]:
                self.assertNotIn(token, line)
                self.assertNotIn(basic, line)
            self.assertNotIn(token, out[0])
            with open(os.path.join(dest, ".git", "config"), encoding="utf-8") as fh:
                config = fh.read()
            for leaked in (token, basic, "extraheader", "x-access-token"):
                self.assertNotIn(leaked.lower(), config.lower())

    def test_refuses_to_run_without_a_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k != "SOURCE_TOKEN"}
            r = subprocess.run(
                ["bash", os.path.join(SCRIPTS, "fetch-source.sh"), "kodflow/x", "0" * 40, tmp],
                capture_output=True, text=True, env=env, check=False,
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(os.listdir(tmp), [])


class Admit(unittest.TestCase):
    """scripts/admit.sh decides what of a dispatch payload reaches the jobs
    holding a token. These pin what it lets through and what it refuses."""

    SPECS = (
        "--run-starts-request",
        "request_id=[0-9]{1,20}-[0-9]{1,4}-[a-z]{1,20}",
        "sha=[0-9a-f]{40}",
        "run_id=[0-9]{1,20}",
        "only?=[a-z0-9 -]{0,40}",
    )
    GOOD = {"request_id": "123-1-openbsd", "sha": "a" * 40, "run_id": "123"}

    def admit(self, payload, *specs):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out")
            text = payload if isinstance(payload, str) else json.dumps(payload)
            r = run("admit.sh", *(specs or self.SPECS), env={"PAYLOAD": text, "GITHUB_OUTPUT": out})
            outputs = {}
            if os.path.exists(out):
                with open(out, encoding="utf-8") as fh:
                    outputs = dict(line.split("=", 1) for line in fh.read().splitlines())
            return r, outputs

    def test_admits_a_good_payload(self):
        r, out = self.admit(dict(self.GOOD, ignored="anything\nat all"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(out, dict(self.GOOD, only=""))
        self.assertNotIn("ignored", r.stdout)

    def test_numbers_are_values(self):
        r, out = self.admit(dict(self.GOOD, run_id=123))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(out["run_id"], "123")

    def test_refusals(self):
        cases = {
            "missing field": {k: v for k, v in self.GOOD.items() if k != "sha"},
            "short sha": dict(self.GOOD, sha="abc"),
            "newline smuggles an output": dict(self.GOOD, only="x\nsha=" + "b" * 40),
            "trailing newline": dict(self.GOOD, request_id="123-1-openbsd\n"),
            "carriage return": dict(self.GOOD, only="x\ry"),
            "object for a value": dict(self.GOOD, sha={"a": 1}),
            "array for a value": dict(self.GOOD, only=["x"]),
            "request names another run": dict(self.GOOD, request_id="124-1-openbsd"),
            "shell metacharacters": dict(self.GOOD, only="$(id)"),
        }
        for name, payload in cases.items():
            with self.subTest(name):
                r, out = self.admit(payload)
                self.assertNotEqual(r.returncode, 0, name)
                self.assertEqual(out, {}, f"{name}: nothing may be written when a check fails")

    def test_not_an_object(self):
        for payload in ("", "null", "[]", '"x"', "{not json"):
            with self.subTest(payload):
                r, out = self.admit(payload)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(out, {})

    def test_allowlist(self):
        specs = ("--allow", "source_repo=kodflow/a,kodflow/b", "source_repo=[a-z]+/[a-z]+")
        r, out = self.admit({"source_repo": "kodflow/b"}, *specs)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(out, {"source_repo": "kodflow/b"})
        for bad in ("kodflow/c", "kodflow/a,kodflow/b", "kodflow"):
            with self.subTest(bad):
                r, out = self.admit({"source_repo": bad}, *specs)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(out, {})

    def test_regex_is_anchored(self):
        r, _ = self.admit({"sha": "a" * 40 + "z"}, "sha=[0-9a-f]{40}")
        self.assertNotEqual(r.returncode, 0)
        r, _ = self.admit({"sha": "z" + "a" * 40}, "sha=[0-9a-f]{40}")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
