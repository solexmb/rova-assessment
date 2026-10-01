# Task 3 - HTTP health-check script

## Overview
`health_check.py` is a dependency-free Python 3 script (stdlib only, so it runs on any cron host, CI runner or container without `pip install`).
For each endpoint it reports **status code**, **response time (ms)** and **pass/fail vs an expected status**. Failures are retried with
**exponential backoff**, then a **JSON summary** is printed and the process **exits non-zero** if anything is still failing.

*Why Python over Bash:* real JSON in/out, timeouts, parallelism and unit tests without `jq`/`curl` version quirks.

## Usage
```bash
cd task-3-scripting

# Option A: config file (see endpoints.example.json)
python3 health_check.py --config endpoints.example.json

# Option B: environment variable - comma-separated URLs (expects 200) ...
HC_ENDPOINTS="https://example.com,http://localhost:8080/health" python3 health_check.py
# ... or a JSON array when you need per-endpoint expected status / timeout
HC_ENDPOINTS='[{"name":"gone","url":"https://example.com/x","expected_status":404}]' python3 health_check.py

# Useful pipelines
python3 health_check.py --config endpoints.json | jq '.summary'
python3 health_check.py --config endpoints.json --output results.json || echo "UNHEALTHY"
```

| Setting | Env var | CLI flag | Default |
|---|---|---|---|
| Config file | `HC_CONFIG` | `--config` | - |
| Endpoints inline | `HC_ENDPOINTS` | - | - |
| Retries after first attempt | `HC_RETRIES` | `--retries` | 2 |
| Initial backoff (s) | `HC_BACKOFF_SECONDS` | `--backoff` | 1.0 |
| Backoff multiplier | `HC_BACKOFF_FACTOR` | `--backoff-factor` | 2.0 |
| Max single delay (s) | `HC_MAX_BACKOFF_SECONDS` | `--max-backoff` | 30 |
| Request timeout (s) | `HC_TIMEOUT` | `--timeout` | 5 |
| Parallel checks | `HC_CONCURRENCY` | `--concurrency` | 10 |

CLI flags override env vars. `expected_status` may be an int or a list (e.g. `[200, 204]`). Redirects are **not** followed, so a
`301` is reported as a `301` (and fails unless you expect it) - a silent redirect to a login page is a classic false "healthy".

**Exit codes:** `0` all passed - `1` at least one endpoint failing after retries - `2` configuration error.
Logs go to **stderr**, the JSON to **stdout**, so piping to `jq` or a file is safe.

### Example output (real run against the Task 2 API; `history` omitted for brevity)
```json
{
  "timestamp": "2026-10-01T07:41:49.748615+00:00",
  "duration_seconds": 0.21,
  "summary": { "total": 2, "passed": 1, "failed": 1, "failed_endpoints": ["broken"], "ok": false },
  "results": [
    { "name": "api",    "url": "http://127.0.0.1:8099/health", "expected_status": [200],
      "status_code": 200, "response_time_ms": 5.9, "passed": true,  "attempts": 1, "error": null },
    { "name": "broken", "url": "http://127.0.0.1:8099/nope",   "expected_status": [200],
      "status_code": 404, "response_time_ms": 1.4, "passed": false, "attempts": 2,
      "error": "unexpected status 404, expected [200]" }
  ]
}
```
Each result also carries a per-attempt `history` array (status, time, error for every try).

## Tests
```bash
cd task-3-scripting && python3 -m unittest discover -s tests -t . -v     # 14 tests, spin up a local HTTP server
```
Covers: pass, fail-after-N-retries (attempt count), recovery on retry, non-200 expectations, redirects, connection refused, the
backoff schedule (exponential + capped), config file/env parsing, and exit codes 0/1/2.

## Design decisions & trade-offs
- **Thread pool** for parallelism - total runtime is bounded by the slowest endpoint rather than the sum. Results keep config order.
- **Response time = last attempt's time**; every attempt's time is in `history`. Time covers connect + TLS + first 4 KB of the body, not full download.
- **Retries on any non-expected result** (including 4xx). Simple and predictable; a refinement is to retry only on 5xx/timeouts, since a 404 will not heal.
- **JSON config, not YAML**, to stay dependency-free.
- **Stdout = data, stderr = logs**, so it composes in pipelines.

## From script to production monitoring/alerting

This script is a good smoke/operational check, but production monitoring needs more. How I would extend it:

1. **Run it on a schedule and publish metrics.**  
   Run as a long‑lived exporter (recommended) or as a CronJob that pushes results every 1–5 minutes; publish metrics such as:
   - `health_check_up{endpoint="<name>",region="<vantage>"}` (gauge: `1` = up, `0` = down)
   - `health_check_latency_seconds_bucket{endpoint="<name>"}` (histogram) or `health_check_latency_seconds` (summary)

   Keep labels small and bounded; avoid using the full URL as a free‑form label. If you run ephemeral CronJobs, either push to a Pushgateway or use a sidecar/long‑lived exporter so the monitoring system can scrape metrics.

2. **Alert on metrics, not single runs.**  
   Use Alertmanager (or your alerting system) with rules like “endpoint down in 3 of the last 5 runs” and policies for severity, deduplication, and escalation (Email / PagerDuty / Slack). Aggregating across runs and regions reduces flapping and false positives. Also alert on high attempt counts or rising latency (p95/p99) to catch degradations before outright failure.

3. **Go beyond status codes.**  
   Add checks for TLS certificate expiry, latency SLO percentiles (p95/p99), and optionally response‑body assertions or multi‑step synthetic transactions for critical paths.

### Limitations of this script

- **Point-in-time and stateless:** no history, trends, de-duplication, flap detection, or alert routing. Rely on metrics and a time-series backend for trend analysis and deduplication.
- **Shallow checks:** it validates status and latency of one GET, not correctness, dependencies, TLS expiry, or user journeys. A `200` from a broken page passes.
- **Self-monitoring problem:** if the scheduler dies, nothing alerts — add a heartbeat or dead-man’s switch.
- **Retries mask intermittent failures:** a flaky service that passes on retry 2 is reported healthy; expose attempt counts and alert on consistently high attempt counts.
- **Scale:** one process with a thread pool is fine for tens of endpoints; for hundreds or thousands, prefer a scalable exporter or distributed probing solution.