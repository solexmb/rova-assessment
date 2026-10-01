import io
import json
import unittest
from wsgiref.util import setup_testing_defaults

from service.main import application


def call(method, path, body=None):
    """Invoke the WSGI app directly; returns (status_code, headers, json_body)."""
    raw = json.dumps(body).encode() if body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    setup_testing_defaults(environ)
    captured = {}

    def start_response(status, headers):
        captured["status"] = int(status.split()[0])
        captured["headers"] = dict(headers)

    payload = b"".join(application(environ, start_response))
    return captured["status"], captured["headers"], json.loads(payload)


class ApiTests(unittest.TestCase):
    def test_health(self):
        status, headers, body = call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_greet(self):
        status, _, body = call("GET", "/api/v1/greet/Ada")
        self.assertEqual(status, 200)
        self.assertEqual(body["message"], "Hello, Ada!")

    def test_greet_rejects_bad_name(self):
        status, _, _ = call("GET", "/api/v1/greet/" + "x" * 33)
        self.assertEqual(status, 400)

    def test_sum(self):
        status, _, body = call("POST", "/api/v1/sum", {"numbers": [1, 2, 3.5]})
        self.assertEqual(status, 200)
        self.assertEqual(body["sum"], 6.5)

    def test_sum_validation(self):
        for bad in ({"numbers": []}, {"numbers": ["a"]}, {"numbers": [True]}, {"nope": 1}, [1, 2]):
            with self.subTest(bad=bad):
                status, _, _ = call("POST", "/api/v1/sum", bad)
                self.assertEqual(status, 400)

    def test_sum_requires_body(self):
        status, _, _ = call("POST", "/api/v1/sum")
        self.assertEqual(status, 400)

    def test_method_not_allowed(self):
        status, headers, _ = call("POST", "/health")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "GET")

    def test_not_found(self):
        status, _, _ = call("GET", "/nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
