#!/usr/bin/env python3
"""Runs the checker as a subprocess against real sockets: python3 test_verify_egress.py"""
import importlib.util
import json
import pathlib
import socket
import subprocess
import sys
import unittest
from unittest import mock

CHECKER = pathlib.Path(__file__).with_name("verify_egress.py")


def run(targets):
    return subprocess.run(
        [sys.executable, "-I", str(CHECKER)],
        input=json.dumps(targets),
        capture_output=True,
        text=True,
        timeout=30,
    )


class VerifyEgress(unittest.TestCase):
    def setUp(self):
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(self.listener.close)
        self.open_port = self.listener.getsockname()[1]
        with socket.create_server(("127.0.0.1", 0)) as spare:
            self.closed_port = spare.getsockname()[1]

    def target(self, name, port, expect):
        return {"name": name, "host": "127.0.0.1", "port": port, "expect": expect}

    def test_exits_zero_when_every_target_matches(self):
        result = run([self.target("open", self.open_port, "allow")])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_refused_connection_does_not_count_as_denied(self):
        # A refusal means the packet reached the host, so the policy didn't stop it.
        result = run([self.target("refused", self.closed_port, "deny")])
        self.assertEqual(result.returncode, 1)
        self.assertIn("got=refused", result.stdout)

    def test_a_dns_failure_does_not_count_as_denied(self):
        result = run([{"name": "typo", "host": "no-such-host.invalid", "port": 443, "expect": "deny"}])
        self.assertEqual(result.returncode, 1)
        self.assertIn("got=error", result.stdout)

    def test_exits_one_when_a_denied_target_is_reachable(self):
        result = run([self.target("leak", self.open_port, "deny")])
        self.assertEqual(result.returncode, 1)
        self.assertIn("leak", result.stdout)
        self.assertIn("MISMATCH", result.stdout)

    def test_exits_one_when_an_allowed_target_is_unreachable(self):
        result = run([self.target("missing", self.closed_port, "allow")])
        self.assertEqual(result.returncode, 1)
        self.assertIn("MISMATCH", result.stdout)

    def test_prints_one_line_per_target(self):
        result = run([self.target("a", self.open_port, "allow"), self.target("b", self.open_port, "allow")])
        self.assertEqual(len(result.stdout.strip().splitlines()), 2)

    def test_exits_two_on_an_empty_target_list(self):
        self.assertEqual(run([]).returncode, 2)

    def test_exits_two_on_an_unknown_expectation(self):
        self.assertEqual(run([self.target("typo", self.open_port, "alow")]).returncode, 2)


class Classify(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("verify_egress", CHECKER)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_only_a_timeout_counts_as_denied(self):
        # The policy drops packets silently, which a client sees as a timeout.
        with mock.patch("socket.create_connection", side_effect=TimeoutError):
            self.assertEqual(self.module.probe("10.0.0.1", 443), "deny")


if __name__ == "__main__":
    unittest.main()
