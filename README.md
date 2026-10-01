# Cloud & Infrastructure Practical Assessment

One repository containing all three tasks. Each task folder has its own README with full details.

| Task | Folder | What it is |
|---|---|---|
| 1 | [`task-1-iac/`](task-1-iac) | Highly available web app on AWS, provisioned with Terraform |
| 2 | [`task-2-cicd/`](task-2-cicd) | Containerised API with a test, scan and push pipeline (GitHub Actions to Amazon ECR) |
| 3 | [`task-3-scripting/`](task-3-scripting) | HTTP health-check script with retries, backoff and JSON output |

```
.
├── .github/workflows/     CI for Task 2
├── task-1-iac/            Terraform: VPC, ALB, ASG, remote state (bootstrap/)
├── task-2-cicd/           API, Dockerfile, unit tests
└── task-3-scripting/      health_check.py, tests, example config
```

## 1. Overview

- **Task 1: Terraform on AWS.** A VPC with public and private subnets across 2 AZs, an internet-facing Application Load
  Balancer, and an Auto Scaling Group in private subnets (no public IPs) that scales on CPU. State is stored in S3 with
  DynamoDB locking. *Why:* Terraform and native AWS services are the most direct fit for the brief, with no extra platform to run or pay for.
- **Task 2: GitHub Actions and Amazon ECR.** A small Python REST API in a multi-stage, non-root Docker image. The pipeline
  runs unit tests, builds the image, scans it with Trivy, and pushes to ECR on `main`. HIGH/CRITICAL findings and failing tests
  fail the build. *Why:* GitHub Actions runs in the same repo, and Trivy is a free scanner covering OS and library CVEs.
- **Task 3: Python.** A dependency-free health checker driven by a config file or environment variable. *Why:* real JSON output,
  timeouts and unit tests without relying on shell tooling.

## 2. Architecture

- **Task 1:** see the diagram in [`task-1-iac/README.md`](task-1-iac/README.md).
- **Task 2:** see the pipeline flow in [`task-2-cicd/README.md`](task-2-cicd/README.md). In short:

```
push to main -> unit tests -> build image -> Trivy scan (fail on HIGH/CRITICAL) -> push to ECR (only if everything passed)
```

The staging-to-production promotion process is documented in the Task 2 README as a design. It is not implemented in the workflow.

## 3. Setup & run

Prerequisites: Terraform >= 1.5, Docker, Python 3.10+, AWS CLI, an AWS account with credentials in your environment
(`AWS_PROFILE` or `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`), and a GitHub repository.
Credentials are never committed.

Exact commands for each task are in its own README:

- Task 1: [`task-1-iac/README.md`](task-1-iac/README.md)
- Task 2: [`task-2-cicd/README.md`](task-2-cicd/README.md)
- Task 3: [`task-3-scripting/README.md`](task-3-scripting/README.md)

## 4. Design decisions & trade-offs

### Key decisions

- **Terraform state backend as a separate bootstrap stack.** A backend cannot create itself, so a small stack creates the
  S3 bucket (versioned, encrypted, public access blocked) and the DynamoDB lock table first. Trade-off: one extra manual step.
- **Private application tier.** Instances have no public IP and accept traffic only from the ALB's security group. Access
  is through SSM rather than SSH. Trade-off: outbound internet needs a NAT gateway, which is not free tier.
- **Single NAT gateway by default.** Cheapest option, but it is a single-AZ dependency for outbound traffic. It is a
  variable, so one NAT per AZ is a one-line change for production.
- **Standard-library Python for the API and the health checker.** Few dependencies means a small attack surface, clean
  scans and nothing to install. Trade-off: a real service would likely use a framework.
- **Tests run in the pipeline before the image is pushed, and the scan gates the push.** A failure at any stage stops the
  pipeline before anything reaches ECR.
- **`ignore-unfixed: true` on the Trivy scan.** The build blocks only on vulnerabilities that have a fix available, so it is
  not permanently red over base-image issues the team cannot fix yet. Trade-off: unfixed HIGH issues are tolerated until a fix exists.

### What I would do with more time

**CI for the Terraform code.** Right now Task 1 is deployed from a laptop. I would add a pipeline for it:

- on every pull request: `terraform fmt -check`, `terraform validate`, `tflint`, and a security scan such as `checkov` or `tfsec`;
- run `terraform plan` on the pull request and post the result as a comment, so reviewers see exactly what would change;
- run `terraform apply` only after merge to `main`, through a protected GitHub environment with required reviewers;
- a scheduled `terraform plan` job to detect drift, and a cost estimate such as Infracost on pull requests.

**Reusable, versioned workflows shared across services.** The Task 2 workflow is written for a single service. I would turn
the common parts (test, build, scan, push, deploy) into reusable workflows that any service can call:

- put them in a central repository and expose them with `on: workflow_call`, with inputs (image name, build context, region,
  severity threshold) and explicit secrets, so each service's own workflow shrinks to a few lines;
- version them with git tags using semantic versioning (`v1.4.2`) plus a moving major tag (`v1`). Services call
  `uses: <org>/<workflows-repo>/.github/workflows/build-scan-push.yml@v1` and are only affected by breaking changes when they choose to move to `v2`;
  security-sensitive consumers can pin to a full commit SHA instead;
- a Terraform equivalent (shared plan/apply workflow) so infrastructure repos get the same checks.

Trade-off: a shared workflow is a shared dependency. A bad release affects every service that follows the moving tag, which is why
versioning, testing and pinning options matter.

**Other improvements:**

- HTTPS on the ALB (ACM certificate, HTTP-to-HTTPS redirect) and a WAF.
- Image signing (cosign), SBOM generation, and Dependabot for dependencies and base images.
- Implement the staging and production deployments described in the Task 2 README.
- Split Terraform into modules with per-environment state
- Extend the health checker into scheduled monitoring with metrics and alerting 

## 5. Assumptions

- Region `us-east-1`, and an AWS account where `t3.micro` is free-tier eligible.
- The demo uses plain HTTP because no domain or certificate was available.
- For Task 2, the Amazon ECR repository and an IAM user with push-only permissions already exist. Provisioning them is out of scope.
- The pipeline triggers on every push to `main`.
- Staging and production deployment targets (ECS, Kubernetes or similar) are out of scope. Promotion is documented as a design.

## 6. Cleanup

Tear down in this order, then confirm in the console that no ALB, NAT gateway, Elastic IP, EC2 instance or ECR repository remains:

```bash
# Task 1: application stack first, then the state backend
cd task-1-iac && terraform destroy
cd bootstrap && terraform apply -var force_destroy=true -auto-approve && terraform destroy

# Task 2: only if you created an ECR repository for testing
aws ecr delete-repository --repository-name ha-web-api --region us-east-1 --force
```

Then delete the IAM access keys and GitHub secrets you created for the pipeline. Task 3 provisions nothing.