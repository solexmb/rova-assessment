# Task 1 - Highly available web app on AWS with Terraform

## Overview
Terraform provisions a VPC, an internet-facing Application Load Balancer, and an
Auto Scaling Group of EC2 instances running a static page. Instances sit in
**private subnets across 2 AZs** with **no public IP**, and scale on **average CPU**.
State is stored remotely in **S3 with DynamoDB locking**.

*Why these tools:* Terraform (required) + AWS ALB/ASG/EC2, the most direct match to
the brief, with no extra platform to learn or pay for (vs. EKS/ECS which add control-plane cost).

## Architecture

```
                         Internet
                            |
                   +--------v--------+
                   | Internet Gateway|
                   +--------+--------+
                            |
 VPC 10.0.0.0/16            |
 +--------------------------v-------------------------------------------+
 |   PUBLIC subnets                                                     |
 |   +-------------------- ALB (internet-facing, SG: 80 from 0.0.0.0/0) -+
 |   |  10.20.0.0/24 (AZ-a)  <--- ALB nodes --->  10.20.1.0/24 (AZ-b)     |
 |   |      [NAT GW]  (1 by default; 1 per AZ optional)                 |
 +---+------------------------------+-----------------------------------+
     | HTTP :80 (SG: only from ALB SG)                  |
 +---v--------------------------+  +--------------------v--------------+
 |  PRIVATE 10.20.10.0/24 (AZ-a) |  |  PRIVATE 10.20.11.0/24 (AZ-b)      |
 |  EC2 (no public IP)          |  |  EC2 (no public IP)               |
 +---------------+--------------+  +--------------------+--------------+
                 \                                      /
                  +----- Auto Scaling Group (min 2, max 4) -----+
                  |  target tracking: avg CPU = 50%             |
                  +---------------------------------------------+

 Remote state (outside the VPC):  S3 bucket (versioned, encrypted)  +  DynamoDB lock table
 Access to instances: SSM Session Manager (no SSH, no port 22)
```

### Requirement -> where it lives
| Requirement | Implementation |
|---|---|
| Terraform on AWS | `*.tf` in this folder |
| App behind load balancer | `alb.tf` (ALB, target group w/ health checks, listener) |
| >= 2 AZs | `az_count` (default 2) drives subnets + `vpc_zone_identifier` |
| ASG scaling on CPU | `compute.tf` -> `aws_autoscaling_policy.cpu` (`ASGAverageCPUUtilization`) |
| VPC, public + private subnets, no public IP on app tier | `network.tf`; `associate_public_ip_address = false` in the launch template; app SG accepts traffic only from the ALB SG |
| Tags: project, environment, owner | provider `default_tags` + explicit tags on ASG/launch-template (`main.tf`, `compute.tf`) |
| Remote state + locking | `bootstrap/` creates S3 + DynamoDB; `versions.tf` uses the `s3` backend |

## Prerequisites
- Terraform >= 1.5, AWS CLI v2, an AWS account with credentials configured
  (`aws configure` or `AWS_PROFILE`/`AWS_ACCESS_KEY_ID`... environment variables - **never commit these**).
- Permissions to create VPC, EC2, ELB, IAM roles, S3, DynamoDB.

## Setup & run (from scratch)

```bash
export AWS_REGION=us-east-1          # must match aws_region below
aws sts get-caller-identity          # sanity check credentials

# 1) Create the remote-state backend (one-off, local state)
cd task-1-iac/bootstrap
cp terraform.tfvars.example terraform.tfvars   # edit owner
terraform init
terraform apply
terraform output -raw backend_hcl > ../backend.hcl   # git-ignored

# 2) Deploy the application stack (state now goes to S3 with locking)
cd ..
cp terraform.tfvars.example terraform.tfvars   # edit owner (and cost levers)
terraform init -backend-config=backend.hcl
terraform fmt -check && terraform validate
terraform plan -out tfplan
terraform apply tfplan

## Design decisions & trade-offs
- **Target-tracking scaling** rather than step scaling: simpler, self-managing alarms, avoids flapping.
- **ELB health checks on the ASG** so unhealthy app instances (not just dead hosts) get replaced; `instance_refresh` gives rolling updates when the AMI/user-data changes.
- **Security**: SG-to-SG rules (app only reachable from the ALB), IMDSv2 required, encrypted gp3 volumes, SSM instead of SSH, default SG emptied, ALB drops invalid headers.
- **Single NAT gateway by default**: cheapest egress; it is a single-AZ dependency for *outbound* traffic only (inbound serving is unaffected). `single_nat_gateway = false` gives one per AZ.
- **State**: S3 versioning (recover bad state), SSE, TLS-only policy, public access blocked; DynamoDB on-demand for locks. Bootstrap is a separate stack because a backend can't create itself.

## Assumptions
- Region `us-east-1`, an account where `t3.micro` is free-tier eligible (use `t2.micro` otherwise).
- HTTP only: no domain/ACM certificate was assumed. Production would terminate HTTPS on the ALB.
- Terraform >= 1.5 and AWS provider 5.x. x86_64 instance types.
- The ALB is intentionally public; "no direct public IP" applies to the application tier.

## Cleanup
```bash
cd task-1-iac
terraform destroy                      # 1) app stack first (uses the remote state)

cd bootstrap
terraform apply -var force_destroy=true -auto-approve   # allow bucket to be emptied
terraform destroy                      # 2) then the state bucket + lock table
```
Finally confirm in the console: no ALB, NAT gateway, Elastic IP, or EC2 instances remain.
