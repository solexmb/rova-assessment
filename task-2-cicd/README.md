# Task 2 - Containerised API with a CI pipeline

## Overview

This repository contains a small stateless REST API written in Python (stdlib WSGI + gunicorn), packaged as a multi-stage Docker image, and validated by a GitHub Actions pipeline.

The pipeline currently does the following:

- runs Python unit tests
- builds the runtime Docker image
- scans the image with Trivy
- pushes the built image to Amazon ECR when AWS credentials and repository configuration are present

The workflow lives in the repository root under:

- `.github/workflows/task-2-ci.yml`

This task focuses on the CI/CD pattern, image hardening, and vulnerability gating rather than provisioning AWS infrastructure.
How an image would be promoted from staging to production is documented at the end of this file as a design; it is not
implemented in the workflow.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness/readiness |
| GET | `/version` | returns the git SHA baked into the image at build time |
| GET | `/api/v1/greet/<name>` | validates the name parameter |
| POST | `/api/v1/sum` | accepts JSON like `{"numbers":[1,2.5]}` and returns `{"sum":3.5}` |

## Current pipeline flow

The repository currently contains one workflow:

- `.github/workflows/task-2-ci.yml`

The current job flow is:

1. `test` job
   - checks out the repo
   - installs Python dependencies
   - runs pytest against `task-2-cicd/tests`

2. `build-test-scan-push` job
   - builds the runtime image with Docker
   - runs Trivy on the built image
   - fails the workflow if a fixable HIGH or CRITICAL vulnerability is found
   - if AWS credentials and ECR variables are configured, pushes the image to ECR

Pull requests run the build and scan path, but the ECR push is guarded behind `if: github.event_name == 'push' && github.ref == 'refs/heads/main'`.

## Dockerfile highlights

- Multi-stage build:
  - `builder` installs dependencies into a virtual environment
  - `test` stage runs tests
  - `runtime` stage copies only what the app needs
- Non-root runtime:
  - fixed numeric user `USER 10001:10001`
- Health checks and runtime hygiene:
  - `HEALTHCHECK`
  - slim base image
  - no pip cache in final layer
  - `PYTHONDONTWRITEBYTECODE`

## Prerequisites

To run locally:

- Docker
- Python 3.12
- GitHub repository containing this code

For the ECR push path, the following must exist:

- an Amazon ECR repository
- IAM credentials able to push to it
- GitHub Actions secrets and variables configured

## Local development

### Run unit tests

```bash
cd task-2-cicd
python3 -m unittest discover -s tests -t . -v
```

### Build the image locally

```bash
cd task-2-cicd
docker build -t ha-web-api:local --build-arg GIT_SHA=local .
```

### Run the container locally

```bash
docker run --rm -p 8080:8080 ha-web-api:local
curl localhost:8080/health
curl -X POST localhost:8080/api/v1/sum -d '{"numbers":[1,2,3]}'
docker run --rm --entrypoint id ha-web-api:local -u
```

The last command should return a non-root UID, typically `10001`.

## GitHub Actions configuration

To enable the ECR push path, configure these secrets and variables in GitHub:

| Type | Name | Example value |
|---|---|---|
| Secret | `AWS_ACCESS_KEY_ID` | access key for an IAM user with ECR push permissions |
| Secret | `AWS_SECRET_ACCESS_KEY` | its matching secret key |
| Variable | `AWS_REGION` | `us-east-1` |
| Variable | `ECR_REPOSITORY` | `ha-web-api` |

The workflow is already written to use those values.

## ECR setup

If you want to test the ECR push path in AWS, you can create a repository like this:

```bash
aws ecr create-repository \
  --repository-name ha-web-api \
  --region us-east-1 \
  --image-scanning-configuration scanOnPush=true
```

Use a dedicated IAM principal with least privilege. A typical policy is:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:CompleteLayerUpload",
        "ecr:DescribeImages",
        "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart"
      ],
      "Resource": "arn:aws:ecr:us-east-1:<ACCOUNT_ID>:repository/ha-web-api"
    }
  ]
}
```

Never commit AWS keys into the repo. Keep them in GitHub encrypted secrets.

## Vulnerability policy

- The workflow fails on any fixable HIGH or CRITICAL vulnerability (`severity: HIGH,CRITICAL`).
- This applies to both OS packages and application libraries (`vuln-type: os,library`).
- `ignore-unfixed: true` means only vulnerabilities with a fix available block the build.
- `exit-code: "1"` causes the workflow to fail before the image is pushed.
- Exceptions go in `.trivyignore` with a justification and review date.

## Tagging strategy

The workflow generates an image tag in this format:

- `branch-short_sha-date-run_number`

Example:

- `main-abc1234-20261001-42`

This makes each image easy to trace back to a branch, commit, date, and workflow run.

## Cleanup

If you create an ECR repository for testing and want to remove it later:

```bash
aws ecr delete-repository \
  --repository-name ha-web-api \
  --region us-east-1 \
  --force
```

Then remove any IAM access keys and GitHub secrets that were added for testing.

## Summary

This repo demonstrates:

- a small Python API
- secure multi-stage Docker build
- unit test validation
- image scan gating
- ECR push for a main-branch pipeline

It is a good baseline for a practical container CI pipeline and is ready to extend with the promotion design below,
GitHub OIDC, SBOM generation, and deployment automation.

## Promoting an image from staging to production (design, not implemented)

The workflow in this repo stops at pushing the image to ECR. This section describes how I would extend it to deploy to
staging automatically and to production after an approval. None of it is in `task-2-ci.yml`, and I have not run it.

### Principle: build once, promote the same image

Production never gets a rebuild. It gets exactly the image that passed the unit tests, the Trivy scan and staging.

- The image tag (`branch-short_sha-date-run_number`) is the immutable record of a build. The build job would expose it as
  a job output so every later job handles the same image.
- `staging` and `production` would be moving tags in ECR. Promotion re-points one of them at an existing image by
  copying its manifest, so no new layers are built or pushed and the digest stays identical.

### Proposed pipeline shape

```
test -> build-test-scan-push -> deploy-staging -> [ manual approval ] -> deploy-production
                                  (automatic)       (production env)
```

| Job | Runs when | GitHub environment | Gate |
|---|---|---|---|
| `build-test-scan-push` | every push / PR | none | tests and Trivy must pass |
| `deploy-staging` | push to `main`, after the build job succeeds | `staging` | none (automatic) |
| `deploy-production` | after `deploy-staging` succeeds | `production` | required reviewers |

Each job would depend on the previous one with `needs:`, so a failure anywhere stops everything after it. The production
job could not even be queued unless staging deployed and passed its smoke test.

### Sketch of the extra jobs

```yaml
  deploy-staging:
    needs: build-test-scan-push
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    environment: staging
    ### staging deploy

  deploy-production:
    needs: [build-test-scan-push, deploy-staging]
    runs-on: ubuntu-latest
    environment: production        # the approval gate attaches here
    #### production deploy

```

### Approval gate with GitHub environments

Configured once under Settings -> Environments:

1. Create a `staging` environment with no protection rules.
2. Create a `production` environment and enable:
   - **Required reviewers**: the specific people or team allowed to approve (for example release managers).
   - **Prevent self-review**: the person who pushed the change cannot approve their own production deploy.
   - **Deployment branches**: restrict to `main`.
3. Store the AWS credentials and the application URL as **environment** secrets and variables, with separate
   least-privilege credentials for production. Environment secrets are only released to the job after approval, so
   production credentials cannot be used before a reviewer signs off.

What this would enforce:

- **Ordering:** `needs: deploy-staging` makes production wait for a successful staging deploy.
- **Who approves:** the production job pauses as "Waiting for review" and only the listed reviewers can release it.
  GitHub records who approved and when.
- **Scope of the approval:** only the `deploy-production` job references the `production` environment, so the approval
  covers the staging-to-production step and nothing else.

