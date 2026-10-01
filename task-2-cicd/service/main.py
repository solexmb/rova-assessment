"""Minimal stateless REST API (WSGI, stdlib only).

Endpoints
  GET  /health                -> {"status": "ok"}
  GET  /version               -> {"version": "<APP_VERSION>"}
  GET  /api/v1/greet/<name>   -> {"message": "Hello, <name>!"}
  POST /api/v1/sum            -> body {"numbers": [1, 2.5]} -> {"sum": 3.5}

Served by gunicorn in the container; `python -m service.main` runs a dev server.
"""
import json
import os
import re
from http import HTTPStatus

MAX_BODY_BYTES = 64 * 1024
MAX_NUMBERS = 1000
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_GREET_RE = re.compile(r"^/api/v1/greet/(?P<name>[^/]+)$")


def _respond(start_response, status, payload, extra_headers=None):
    body = json.dumps(payload).encode("utf-8")
    headers = [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
        ("X-Content-Type-Options", "nosniff"),
    ]
    headers.extend(extra_headers or [])
    start_response(f"{status.value} {status.phrase}", headers)
    return [body]


def _not_allowed(start_response, allow):
    return _respond(start_response, HTTPStatus.METHOD_NOT_ALLOWED,
                    {"error": "method not allowed"}, [("Allow", allow)])


def _read_json(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        raise ValueError("invalid Content-Length")
    if length <= 0:
        raise ValueError("request body required")
    if length > MAX_BODY_BYTES:
        raise ValueError("request body too large")
    try:
        return json.loads(environ["wsgi.input"].read(length))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("body must be valid JSON")


def application(environ, start_response):
    method = environ["REQUEST_METHOD"]
    path = environ.get("PATH_INFO", "/")

    if path == "/health":
        if method != "GET":
            return _not_allowed(start_response, "GET")
        return _respond(start_response, HTTPStatus.OK, {"status": "ok"})

    if path == "/version":
        if method != "GET":
            return _not_allowed(start_response, "GET")
        return _respond(start_response, HTTPStatus.OK,
                        {"version": os.environ.get("APP_VERSION", "dev")})

    match = _GREET_RE.match(path)
    if match:
        if method != "GET":
            return _not_allowed(start_response, "GET")
        name = match.group("name")
        if not _NAME_RE.match(name):
            return _respond(start_response, HTTPStatus.BAD_REQUEST,
                            {"error": "name must be 1-32 chars of [A-Za-z0-9_-]"})
        return _respond(start_response, HTTPStatus.OK, {"message": f"Hello, {name}!"})

    if path == "/api/v1/sum":
        if method != "POST":
            return _not_allowed(start_response, "POST")
        try:
            data = _read_json(environ)
        except ValueError as exc:
            return _respond(start_response, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        numbers = data.get("numbers") if isinstance(data, dict) else None
        valid = (
            isinstance(numbers, list)
            and 0 < len(numbers) <= MAX_NUMBERS
            and all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in numbers)
        )
        if not valid:
            return _respond(start_response, HTTPStatus.BAD_REQUEST,
                            {"error": f"'numbers' must be a list of 1-{MAX_NUMBERS} numbers"})
        return _respond(start_response, HTTPStatus.OK, {"sum": sum(numbers)})

    return _respond(start_response, HTTPStatus.NOT_FOUND, {"error": "not found"})


if __name__ == "__main__":  # local dev only; production uses gunicorn
    from wsgiref.simple_server import make_server

    port = int(os.environ.get("PORT", "8080"))
    print(f"dev server on http://127.0.0.1:{port}")
    make_server("127.0.0.1", port, application).serve_forever()
