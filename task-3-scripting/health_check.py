#!/usr/bin/env python3
"""HTTP endpoint health checker (stdlib only).

For every endpoint: status code, response time, pass/fail vs. an expected status.
Failures are retried with exponential backoff. A JSON summary goes to stdout
(logs go to stderr, so `health_check.py | jq` works). Exit codes:

    0  every endpoint passed
    1  at least one endpoint still failing after all retries
    2  configuration / usage error

Configuration (CLI flags override environment variables):

    HC_CONFIG               path to a JSON config file (see endpoints.example.json)
    HC_ENDPOINTS            alternative to a file: comma-separated URLs, or a JSON array
    HC_RETRIES              retries AFTER the first attempt            (default 2)
    HC_BACKOFF_SECONDS      delay before the first retry               (default 1.0)
    HC_BACKOFF_FACTOR       multiplier applied per further retry       (default 2.0)
    HC_MAX_BACKOFF_SECONDS  cap on a single delay                      (default 30)
    HC_TIMEOUT              per-request timeout, seconds               (default 5)
    HC_CONCURRENCY          endpoints checked in parallel              (default 10)
"""
import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

EXIT_OK, EXIT_FAILING, EXIT_CONFIG = 0, 1, 2


class ConfigError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report the real status (e.g. 301) instead of silently following redirects."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- config
def _as_status_list(value):
    values = value if isinstance(value, list) else [value]
    if not values or not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        raise ConfigError(f"expected_status must be an int or list of ints, got {value!r}")
    return values


def normalise_endpoints(raw, default_status=200, default_timeout=5.0):
    """Turn user input (strings or dicts) into validated endpoint dicts."""
    endpoints = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict) or not item.get("url"):
            raise ConfigError(f"endpoint #{i} needs a 'url': {item!r}")
        url = item["url"]
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"endpoint #{i} url must start with http:// or https://: {url!r}")
        try:
            timeout = float(item.get("timeout", default_timeout))
        except (TypeError, ValueError):
            raise ConfigError(f"endpoint #{i} has an invalid timeout: {item.get('timeout')!r}")
        endpoints.append({
            "name": item.get("name") or url,
            "url": url,
            "expected_status": _as_status_list(item.get("expected_status", default_status)),
            "timeout": timeout,
        })
    if not endpoints:
        raise ConfigError("no endpoints configured")
    return endpoints


def load_endpoints(config_path, env_endpoints, default_timeout):
    if config_path:
        try:
            with open(config_path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except OSError as exc:
            raise ConfigError(f"cannot read config {config_path}: {exc}")
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config {config_path} is not valid JSON: {exc}")
        if isinstance(cfg, list):
            cfg = {"endpoints": cfg}
        if not isinstance(cfg, dict) or "endpoints" not in cfg:
            raise ConfigError("config must be an object with an 'endpoints' list")
        defaults = cfg.get("defaults", {})
        return normalise_endpoints(
            cfg["endpoints"],
            default_status=defaults.get("expected_status", 200),
            default_timeout=defaults.get("timeout", default_timeout),
        )
    if env_endpoints:
        text = env_endpoints.strip()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"HC_ENDPOINTS is not valid JSON: {exc}")
        else:
            raw = [u.strip() for u in text.split(",") if u.strip()]
        return normalise_endpoints(raw, default_timeout=default_timeout)
    raise ConfigError("no endpoints: set --config/HC_CONFIG or HC_ENDPOINTS")


# --------------------------------------------------------------------------- checking
def probe(url, timeout):
    """One HTTP GET. Returns (status_code | None, elapsed_ms, error | None)."""
    request = urllib.request.Request(url, headers={"User-Agent": "health-check/1.0"})
    status, error = None, None
    start = time.perf_counter()
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status = response.status
            response.read(4096)
    except urllib.error.HTTPError as exc:  # 3xx/4xx/5xx still carry a real status code
        status = exc.code
        exc.close()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        error = f"{type(exc).__name__}: {reason}"
    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    return status, elapsed_ms, error


def check_endpoint(endpoint, retries, backoff, factor, max_backoff, sleep=time.sleep, prober=probe):
    """Check one endpoint, retrying with exponential backoff until it passes."""
    expected = endpoint["expected_status"]
    total_attempts = retries + 1
    history = []
    for attempt in range(1, total_attempts + 1):
        status, elapsed_ms, error = prober(endpoint["url"], endpoint["timeout"])
        passed = status in expected
        if not passed and error is None:
            error = f"unexpected status {status}, expected {expected}"
        history.append({
            "attempt": attempt,
            "status_code": status,
            "response_time_ms": elapsed_ms,
            "error": None if passed else error,
        })
        log(f"[{endpoint['name']}] attempt {attempt}/{total_attempts}: "
            f"status={status} time={elapsed_ms}ms {'PASS' if passed else 'FAIL ' + str(error)}")
        if passed:
            break
        if attempt < total_attempts:
            delay = min(backoff * (factor ** (attempt - 1)), max_backoff)
            log(f"[{endpoint['name']}] retrying in {delay:.1f}s")
            sleep(delay)
    last = history[-1]
    return {
        "name": endpoint["name"],
        "url": endpoint["url"],
        "expected_status": expected,
        "status_code": last["status_code"],
        "response_time_ms": last["response_time_ms"],
        "passed": last["error"] is None,
        "attempts": len(history),
        "error": last["error"],
        "history": history,
    }


def run_checks(endpoints, retries, backoff, factor, max_backoff, concurrency, sleep=time.sleep):
    workers = max(1, min(concurrency, len(endpoints)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(check_endpoint, ep, retries, backoff, factor, max_backoff, sleep)
                   for ep in endpoints]
        return [f.result() for f in futures]  # preserves config order


def build_summary(results, started_at, duration_s):
    failed = [r["name"] for r in results if not r["passed"]]
    return {
        "timestamp": started_at.isoformat(),
        "duration_seconds": round(duration_s, 3),
        "summary": {
            "total": len(results),
            "passed": len(results) - len(failed),
            "failed": len(failed),
            "failed_endpoints": failed,
            "ok": not failed,
        },
        "results": results,
    }


# --------------------------------------------------------------------------- CLI
def _env(name, default, cast):
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ConfigError(f"environment variable {name}={raw!r} is not a valid {cast.__name__}")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="HTTP endpoint health checker with retries.")
    parser.add_argument("--config", default=os.environ.get("HC_CONFIG"), help="JSON config file (env: HC_CONFIG)")
    parser.add_argument("--retries", type=int, help="retries after first attempt (env: HC_RETRIES, default 2)")
    parser.add_argument("--backoff", type=float, help="initial backoff seconds (env: HC_BACKOFF_SECONDS, default 1)")
    parser.add_argument("--backoff-factor", type=float, help="backoff multiplier (env: HC_BACKOFF_FACTOR, default 2)")
    parser.add_argument("--max-backoff", type=float, help="max single delay (env: HC_MAX_BACKOFF_SECONDS, default 30)")
    parser.add_argument("--timeout", type=float, help="request timeout seconds (env: HC_TIMEOUT, default 5)")
    parser.add_argument("--concurrency", type=int, help="parallel checks (env: HC_CONCURRENCY, default 10)")
    parser.add_argument("--output", help="also write the JSON summary to this file")
    return parser.parse_args(argv)


def main(argv=None, sleep=time.sleep):
    try:
        args = parse_args(argv)
        pick = lambda cli, name, default, cast: cli if cli is not None else _env(name, default, cast)
        retries = pick(args.retries, "HC_RETRIES", 2, int)
        backoff = pick(args.backoff, "HC_BACKOFF_SECONDS", 1.0, float)
        factor = pick(args.backoff_factor, "HC_BACKOFF_FACTOR", 2.0, float)
        max_backoff = pick(args.max_backoff, "HC_MAX_BACKOFF_SECONDS", 30.0, float)
        timeout = pick(args.timeout, "HC_TIMEOUT", 5.0, float)
        concurrency = pick(args.concurrency, "HC_CONCURRENCY", 10, int)
        if retries < 0 or backoff < 0 or factor < 1 or timeout <= 0 or concurrency < 1:
            raise ConfigError("retries/backoff must be >= 0, factor >= 1, timeout > 0, concurrency >= 1")
        endpoints = load_endpoints(args.config, os.environ.get("HC_ENDPOINTS"), timeout)
    except ConfigError as exc:
        log(f"configuration error: {exc}")
        return EXIT_CONFIG

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    results = run_checks(endpoints, retries, backoff, factor, max_backoff, concurrency, sleep)
    report = build_summary(results, started, time.perf_counter() - t0)

    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return EXIT_OK if report["summary"]["ok"] else EXIT_FAILING


if __name__ == "__main__":
    sys.exit(main())
