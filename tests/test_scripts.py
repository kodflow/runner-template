"""What the scripts next to the token may print, checked by running them.

The build scripts need macOS and a private checkout, so they are proven by
the dispatched runs; these are the parts that decide what reaches a public
log, which any Linux box can exercise.

Run: python3 -m unittest discover -s tests
"""

import os
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
