# Cloud & Infrastructure practical assessment

Three tasks, one repo. Each folder has its own README with architecture, exact commands, trade-offs, assumptions and cleanup.

| Task | Folder | What it is | Stack (one-line why) |
|---|---|---|---|
| 1 | [`task-1-iac/`](task-1-iac) | Highly available web app: ALB -> ASG (2 AZs, private subnets, CPU scaling), remote state | Terraform + AWS ALB/ASG/EC2: the direct fit for the brief, no control-plane cost |
| 2 | [`task-2-cicd/`](task-2-cicd) | Multi-stage non-root container + CI/CD: build -> test -> scan -> push to ECR | GitHub Actions + Trivy + ECR: in-repo and free; registry assumed to exist |
| 3 | [`task-3-scripting/`](task-3-scripting) | Health-check script with retries/backoff, JSON summary, exit codes | Python stdlib: portable, testable, zero dependencies |

```
.
├── .github/workflows/        task-2-ci.yml, task-2-promote.yml, task-3-tests.yml
├── task-1-iac/               Terraform app stack + bootstrap/ (S3+DynamoDB state backend)
├── task-2-cicd/              service/ (API), tests/, Dockerfile
└── task-3-scripting/         health_check.py, tests/, endpoints.example.json
```

## Quick start (prerequisites for everything)
- AWS account + CLI credentials in your environment (`AWS_PROFILE` or `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_REGION`). Nothing is committed.
- Terraform >= 1.5, Docker, Python 3.10+, a GitHub repo for the pipeline.

Order I'd run them: **Task 3** (no cloud needed) -> **Task 1** (bootstrap, then app) -> **Task 2** (GitHub secrets/variables, push).
Point Task 3 at the Task 1 ALB URL (`terraform output app_url`) to health-check the deployed stack.

## Cost & cleanup (summary)
Everything is sized for free tier / low cost, **except the NAT gateway** in Task 1 (~$0.045/hr; can be disabled with `enable_nat_gateway=false`).
Tear down in this order and confirm in the console that no ALB / NAT / EIP / EC2 / ECR repo remains:
```bash
(cd task-1-iac && terraform destroy)
(cd task-1-iac/bootstrap && terraform apply -var force_destroy=true -auto-approve && terraform destroy)
aws ecr delete-repository --repository-name ha-web-api --force   # if you created the ECR repo for this exercise
```

## Assumptions (global)
- Region `us-east-1`; a new-ish AWS account where `t3.micro` is free-tier eligible.
- HTTP only for the demo (no domain/certificate); HTTPS is listed as a next step.
- "Push to main" triggers the pipeline literally (no path filter).
- For Task 2 the ECR repository and a push-only IAM user already exist (credentials supplied as GitHub secrets).
- Staging/production *deployment targets* are out of scope; promotion is implemented at the registry level and documented.

## Verification status (please read)
Verified in the authoring environment: **Task 2 API unit tests (8 passing)**, **Task 3 unit tests (14 passing)**, and an end-to-end run of the
health checker against the running API confirming exit codes `0` (healthy), `1` (failing after retries) and `2` (config error).

**Not yet executed** (no AWS account, Docker daemon, GitHub runner or Terraform binary in the authoring sandbox): `terraform validate/plan/apply`
for Task 1, `docker build`, and the GitHub Actions run. Before submitting, run `terraform fmt && terraform validate`, deploy once,
and fix anything the real platforms flag (provider version drift, action versions, account-specific limits such as instance-type eligibility).
Commit `.terraform.lock.hcl` after the first `terraform init`.

## What I'd do with more time (cross-cutting)
HTTPS/WAF, VPC endpoints instead of NAT, CI for Terraform (fmt/validate/tflint/checkov + plan on PR), Terraform modules and per-env
state, image signing + SBOM, a real staging deployment with automated smoke tests (reusing the Task 3 checker as the gate),
and CloudWatch/SNS alerting wired to the health checks.
