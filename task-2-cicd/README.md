# Task 2 - Containerised API with a test -> scan -> push pipeline

## Overview
A small stateless REST API (Python, stdlib WSGI + gunicorn) packaged with a **multi-stage, non-root
Dockerfile**, built/tested/scanned/pushed by **GitHub Actions** to **Amazon ECR**.

*Why:* GitHub Actions runs in the same repo (as requested); Trivy is a single free scanner covering OS + library CVEs.
The ECR repository and AWS credentials are **assumed to already exist** - this task is about the pipeline, not provisioning the registry.

Workflows live in the repo root (GitHub requires `.github/workflows/`):
`.github/workflows/task-2-ci.yml` and `.github/workflows/task-2-promote.yml`.

## Pipeline flow (every push to `main`)

```
 push to main
     |
     v
 [1 Build]  docker build --target runtime  ------------------------------+
     |                                                                    |
     v                                                                    |
 [2 Test ]  docker build --target test   (runs unittest; fail => stop)    |
     |                                                                    |
     v                                                                    |
 [  Smoke]  assert UID != 0, container answers GET /health                |
     |                                                                    |
     v                                                                    |
 [3 Scan ]  Trivy on the built image; any fixable HIGH/CRITICAL => FAIL   |
     |                                                                    |
     v         (only if every step above passed AND event == push to main)|
 [4 Push ]  AWS creds (GitHub secrets) -> ECR login -> push <git-sha> + staging
```
Steps run sequentially in one job, so a failure anywhere stops everything after it; the push steps are the last ones and
carry `if: push && main`. The **same image** that was tested and scanned is the one pushed (no rebuild).
Pull requests run steps 1-3 but never push: the AWS steps are skipped, and GitHub does not expose repository secrets to workflows from forks.

## API
| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness/readiness |
| GET | `/version` | returns git SHA baked in at build |
| GET | `/api/v1/greet/<name>` | validates name |
| POST | `/api/v1/sum` | `{"numbers":[1,2.5]}` -> `{"sum":3.5}` |

## Dockerfile highlights
- **Multi-stage**: `builder` (installs deps into a venv) -> `test` (runs the unit tests; never shipped) -> `runtime` (copies only the venv and app code; no pip cache, no tests, no build tooling).
- **Non-root**: fixed numeric `USER 10001:10001` (verifiable by Kubernetes `runAsNonRoot`); the pipeline asserts it.
- `HEALTHCHECK`, `slim` base, no shell login for the user, `PYTHONDONTWRITEBYTECODE`.

## Prerequisites
- Docker, Python 3.10+, a GitHub repo containing this code.
- **Assumed to exist already (not created by this repo):**
  - An Amazon ECR repository (e.g. `ha-web-api`) in your chosen region.
  - AWS credentials able to push to it (see below).

If you don't have a repository yet, one command creates it (with scan-on-push enabled):
```bash
aws ecr create-repository --repository-name ha-web-api --region us-east-1 \
  --image-scanning-configuration scanOnPush=true
```

## Setup & run

### Run locally
```bash
cd task-2-cicd
python3 -m unittest discover -s tests -t . -v                  # unit tests
docker build -t ha-web-api:local --build-arg GIT_SHA=local .
docker run --rm -p 8080:8080 ha-web-api:local
curl localhost:8080/health
curl -X POST localhost:8080/api/v1/sum -d '{"numbers":[1,2,3]}'
docker run --rm --entrypoint id ha-web-api:local -u            # 10001 (non-root)
```

### Configure GitHub (Settings -> Secrets and variables -> Actions)

| Type | Name | Value |
|---|---|---|
| Secret | `AWS_ACCESS_KEY_ID` | access key of a push-only IAM user |
| Secret | `AWS_SECRET_ACCESS_KEY` | its secret key |
| Variable | `AWS_REGION` | e.g. `us-east-1` |
| Variable | `ECR_REPOSITORY` | e.g. `ha-web-api` |

Use a **dedicated IAM user with least privilege**, not an admin key. Policy (replace region/account/repo):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
    { "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
        "ecr:DescribeImages", "ecr:GetDownloadUrlForLayer", "ecr:InitiateLayerUpload",
        "ecr:PutImage", "ecr:UploadLayerPart"
      ],
      "Resource": "arn:aws:ecr:us-east-1:<ACCOUNT_ID>:repository/ha-web-api" }
  ]
}
```
Keys live only in GitHub encrypted secrets - never in the repo.

Then `git push origin main` and check the **Actions** tab; the run summary prints the pushed image digest.

To verify the gates: (a) break a test assertion -> the job fails before scan/push; (b) add a known-vulnerable pinned dependency
to `requirements.txt` (or use an old base image) -> Trivy fails the job; nothing reaches ECR.

## Vulnerability policy
- Fails on **HIGH or CRITICAL** (`severity: HIGH,CRITICAL`, `exit-code: 1`), for OS packages and libraries.
- `ignore-unfixed: true`: only blocks on vulnerabilities **that have a fix available**. Unfixed base-image CVEs would otherwise make
  the build red with nothing the team can do. This is a deliberate trade-off; if the brief intends *every* HIGH to block, remove that line.
- Exceptions go in `.trivyignore` with justification + review date (currently empty).
- ECR `scan_on_push` is enabled as a second, registry-side check (and continuous scanning is an easy upgrade).

## Promoting an image: staging -> production
**Principle: build once, promote the same immutable artifact.** Never rebuild for production.

1. **Identity.** Every build is tagged with its git SHA and has a content digest (`sha256:...`). Deployments reference the digest/SHA, not a tag that can move.
2. **CI -> staging (automatic).** The push to `main` publishes `<sha>` and moves the `staging` tag. Staging deploys that digest (e.g. ECS service / Kubernetes manifest / Helm values updated by a GitOps commit).
3. **Staging validation.** Smoke tests, integration/e2e tests and (optionally) a soak period run against staging; migrations are rehearsed.
4. **Approval gate.** Run the **`task-2 promote image to production`** workflow with the SHA. It targets the GitHub **`production` environment**, where *required reviewers* are configured, so a human approves.
5. **Promotion = retag, not rebuild.** The workflow copies the staging image manifest to the `production` tag via `ecr put-image` (same digest). The production deploy then rolls out that digest (rolling/blue-green/canary with health checks).
6. **Rollback.** Redeploy the previous known-good digest (still in ECR thanks to the lifecycle policy); the git SHA tag makes it trivial to identify.
7. **Hardening for real production.**
   - Separate AWS accounts for staging and prod (own ECR/roles, blast-radius isolation); promotion via ECR cross-account replication or `crane copy`.
   - Image signing (cosign/Sigstore) at CI, signature verification in the cluster admission controller; SBOM attestation.
   - Re-scan the production tag on a schedule (new CVEs appear after build) and alert/rebuild.
   - Protected branches + required checks, so `main` always reflects reviewed code.

## Design decisions & trade-offs
- **stdlib WSGI + gunicorn**: tiny attack surface and one pinned dependency, keeping scans clean and the image small. A real service would likely use FastAPI/Flask.
- **Tests as a Docker stage**: tests run in the exact environment (Python version, deps) the image uses, with no extra runner setup.
- **Single sequential job** rather than several jobs: guarantees the pushed image is bit-for-bit the one tested/scanned (no artifact hand-off). Cost: less parallelism. With more time I'd split jobs and pass the image as an artifact or use a build cache.
- **Static IAM-user keys in GitHub secrets**: the simplest way to authenticate given the registry is assumed to exist. Trade-off: long-lived credentials that must be rotated. The key is least-privilege (push to one repo only) and is only used on `main` pushes. **Production upgrade: GitHub OIDC with an assumable IAM role**, which needs no stored keys - only `role-to-arn` replaces the two key inputs in the workflow.
- **Mutable ECR tags** so `staging`/`production` can move; SHA tags are the immutable record. Trade-off: mutable repos permit overwriting, so deploy by digest.
- **Pinned action versions** by tag. With more time: pin by commit SHA and enable Dependabot for actions and the base image.

## Assumptions
- "On every push to main" is implemented literally (no path filter), so any push to `main` triggers it.
- The ECR repository and a least-privilege IAM user already exist; region defaults to `us-east-1`.
- Staging/production deploy targets are out of scope; promotion is shown at the registry level and documented above.

## What I'd do with more time
GitHub OIDC instead of static keys, dependency/SBOM scanning (`trivy fs`, Syft), image signing, build cache, multi-arch images, linting (hadolint, ruff), coverage gate, an actual
staging deployment (ECS Fargate) with automated smoke tests, Dependabot, and Slack/SNS notifications on failure.

## Cleanup
This task provisions nothing itself. To avoid ongoing cost and exposure:
```bash
# delete the images (and the repo, if you created it for this exercise)
aws ecr delete-repository --repository-name ha-web-api --region us-east-1 --force
```
Then delete the IAM user/access keys you created for the pipeline, and remove the GitHub secrets and variables.
