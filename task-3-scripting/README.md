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
backoff schedule (exponential + capped), config file/env parsing, and exit codes 0/1/2. Also run in CI by `.github/workflows/task-3-tests.yml`.

## Design decisions & trade-offs
- **Exponential backoff with a cap** protects a struggling service from retry storms and absorbs brief blips (deploys, GC pauses). I did not add jitter; with many checkers hitting one target, add it.
- **Thread pool** for parallelism - total runtime is bounded by the slowest endpoint rather than the sum. Results keep config order.
- **Response time = last attempt's time**; every attempt's time is in `history`. Time covers connect + TLS + first 4 KB of the body, not full download.
- **Retries on any non-expected result** (including 4xx). Simple and predictable; a refinement is to retry only on 5xx/timeouts, since a 404 will not heal.
- **JSON config, not YAML**, to stay dependency-free.
- **Stdout = data, stderr = logs**, so it composes in pipelines.

## From script to production monitoring/alerting
This script is a good *smoke check* or CI/CD gate, but production monitoring needs more. How I would extend it:

1. **Run it on a schedule and ship results.** Run as cron / Kubernetes CronJob / EventBridge-scheduled Lambda or Fargate task every 1-5 min; emit the JSON to CloudWatch Logs, and publish **metrics** (`up`, `latency_ms`, per endpoint) via CloudWatch `put-metric-data` or the Prometheus Pushgateway / a `/metrics` exporter.
2. **Alert on metrics, not on a single run.** CloudWatch Alarms (or Alertmanager) with rules like "failed in 3 of the last 5 runs" -> SNS/PagerDuty/Slack. This removes flapping alerts, adds recovery notifications, deduplication, severity and escalation/on-call routing.
3. **Probe from outside and from several regions** (Route 53 health checks, CloudWatch Synthetics canaries, or runners in 2-3 regions) and require quorum before paging, so a single probe's network issue is not an outage.
4. **Go beyond status codes**: assert on response body/JSON fields, TLS certificate expiry, latency SLO percentiles (p95/p99), and run multi-step synthetic transactions (login -> action).
5. **Operability**: config in Git with review, secrets (auth headers) from Secrets Manager/SSM never from the repo, structured logs with correlation IDs, a dashboard (Grafana/CloudWatch) with SLO/error-budget burn-rate alerts, runbook links in every alert.
6. **Or just adopt a tool**: for most teams, Prometheus Blackbox Exporter + Alertmanager, Uptime Kuma, or a SaaS (Datadog/Pingdom) beats maintaining a bespoke checker. This script's value is as a portable gate and as a learning/baseline tool.

### Limitations of this script
- **Point-in-time and stateless**: no history, trends, de-duplication, flap detection, or alert routing. Alerting is only "the cron job exited 1".
- **Single vantage point**: it sees the network from wherever it runs; it cannot distinguish "service down" from "my egress is broken" or regional issues.
- **Shallow checks**: it validates status and latency of one GET, not correctness, dependencies, TLS expiry, or user journeys. A `200` from a broken page passes.
- **No authentication flows**, custom headers, non-GET methods or client certs (easy to add, not implemented).
- **Self-monitoring problem**: if the scheduler or host dies, nothing alerts (needs a dead-man's-switch/heartbeat).
- **Retries mask intermittent failures** (a flaky service that passes on retry 2 is reported healthy, only visible via `attempts`) - alert on high attempt counts too.
- **Scale**: one process with a thread pool is fine for tens of endpoints, not thousands.

## Cleanup
Nothing is provisioned. Delete any `results.json` you wrote (git-ignored).
