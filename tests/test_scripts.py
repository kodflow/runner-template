"""What the scripts next to the token may print, checked by running them.

The build scripts need macOS and a private checkout, so they are proven by
the dispatched runs; these are the parts that decide what reaches a public
log, which any Linux box can exercise.

Run: python3 -m unittest discover -s tests
"""

import base64
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


if __name__ == "__main__":
    unittest.main()
