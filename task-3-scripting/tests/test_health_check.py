import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from unittest import mock
from contextlib import redirect_stdout, redirect_stderr

import health_check as hc

NO_SLEEP = lambda _s: None


class Handler(BaseHTTPRequestHandler):
    flaky_hits = 0

    def do_GET(self):
        if self.path == "/ok":
            code = 200
        elif self.path == "/boom":
            code = 500
        elif self.path == "/missing":
            code = 404
        elif self.path == "/moved":
            code = 301
        elif self.path == "/flaky":  # fails twice, then recovers
            Handler.flaky_hits += 1
            code = 200 if Handler.flaky_hits > 2 else 503
        else:
            code = 404
        self.send_response(code)
        if code == 301:
            self.send_header("Location", "/ok")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def ep(self, path, expected=200, timeout=2):
        return {"name": path, "url": self.base + path, "expected_status": [expected] if isinstance(expected, int) else expected, "timeout": timeout}


class CheckEndpointTests(ServerCase):
    def run_check(self, ep, retries=2):
        with redirect_stderr(StringIO()):
            return hc.check_endpoint(ep, retries, 1.0, 2.0, 30.0, sleep=NO_SLEEP)

    def test_pass_first_try(self):
        r = self.run_check(self.ep("/ok"))
        self.assertTrue(r["passed"])
        self.assertEqual((r["status_code"], r["attempts"]), (200, 1))
        self.assertGreaterEqual(r["response_time_ms"], 0)

    def test_fails_after_all_retries(self):
        r = self.run_check(self.ep("/boom"), retries=3)
        self.assertFalse(r["passed"])
        self.assertEqual(r["attempts"], 4)  # 1 try + 3 retries
        self.assertEqual(r["status_code"], 500)
        self.assertIn("unexpected status 500", r["error"])

    def test_recovers_on_retry(self):
        Handler.flaky_hits = 0
        r = self.run_check(self.ep("/flaky"), retries=3)
        self.assertTrue(r["passed"])
        self.assertEqual(r["attempts"], 3)

    def test_expected_non_200(self):
        self.assertTrue(self.run_check(self.ep("/missing", expected=404))["passed"])

    def test_redirect_is_not_followed(self):
        r = self.run_check(self.ep("/moved"), retries=0)
        self.assertEqual(r["status_code"], 301)
        self.assertFalse(r["passed"])

    def test_connection_refused_is_reported(self):
        ep = {"name": "dead", "url": "http://127.0.0.1:1/", "expected_status": [200], "timeout": 1}
        r = self.run_check(ep, retries=1)
        self.assertFalse(r["passed"])
        self.assertIsNone(r["status_code"])
        self.assertIn("Error", r["error"])

    def test_backoff_schedule_is_exponential_and_capped(self):
        delays = []
        prober = lambda url, t: (500, 1.0, None)
        with redirect_stderr(StringIO()):
            hc.check_endpoint(self.ep("/x"), 4, 1.0, 2.0, 5.0, sleep=delays.append, prober=prober)
        self.assertEqual(delays, [1.0, 2.0, 4.0, 5.0])


class CliTests(ServerCase):
    def run_main(self, argv, env=None):
        out, err = StringIO(), StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False), redirect_stdout(out), redirect_stderr(err):
            code = hc.main(argv, sleep=NO_SLEEP)
        return code, out.getvalue()

    def clean_env(self, **extra):
        env = {k: "" for k in os.environ if k.startswith("HC_")}
        env.update(extra)
        return env

    def test_all_pass_exit_0_and_json(self):
        code, out = self.run_main([], self.clean_env(HC_ENDPOINTS=f"{self.base}/ok,{self.base}/ok"))
        report = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(report["summary"]["ok"])
        self.assertEqual(report["summary"]["passed"], 2)

    def test_any_failure_exit_1(self):
        env = self.clean_env(HC_ENDPOINTS=f"{self.base}/ok,{self.base}/boom", HC_RETRIES="1")
        code, out = self.run_main([], env)
        report = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertEqual(report["summary"]["failed_endpoints"], [f"{self.base}/boom"])

    def test_config_file(self):
        import tempfile
        cfg = {"defaults": {"timeout": 2}, "endpoints": [
            {"name": "good", "url": self.base + "/ok"},
            {"name": "gone", "url": self.base + "/missing", "expected_status": 404}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(cfg, fh)
        try:
            code, out = self.run_main(["--config", fh.name, "--retries", "0"], self.clean_env())
        finally:
            os.unlink(fh.name)
        self.assertEqual(code, 0)
        self.assertEqual([r["name"] for r in json.loads(out)["results"]], ["good", "gone"])

    def test_json_env_endpoints(self):
        env = self.clean_env(HC_ENDPOINTS=json.dumps([{"url": self.base + "/missing", "expected_status": 404}]))
        code, _ = self.run_main([], env)
        self.assertEqual(code, 0)

    def test_missing_config_exit_2(self):
        code, out = self.run_main([], self.clean_env())
        self.assertEqual(code, 2)
        self.assertEqual(out, "")

    def test_bad_env_number_exit_2(self):
        code, _ = self.run_main([], self.clean_env(HC_ENDPOINTS=self.base + "/ok", HC_RETRIES="abc"))
        self.assertEqual(code, 2)

    def test_bad_url_exit_2(self):
        code, _ = self.run_main([], self.clean_env(HC_ENDPOINTS="ftp://nope"))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
