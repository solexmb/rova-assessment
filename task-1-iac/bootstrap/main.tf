##############################################################################
# Bootstrap: remote-state backend (S3 bucket + DynamoDB lock table)
#
# This tiny stack is applied ONCE with LOCAL state (chicken-and-egg: the backend
# cannot store its own state before it exists). Its state file is git-ignored
# and holds no secrets - only resource IDs. Everything else in task-1-iac uses
# the S3 backend this creates.
##############################################################################

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project     = var.project
      environment = var.environment
      owner       = var.owner
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # S3 bucket names are global; the account ID makes it unique and predictable.
  bucket_name = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}"
  lock_table  = "${var.project}-tfstate-lock"
}

resource "aws_s3_bucket" "state" {
  bucket        = local.bucket_name
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled" # lets you recover from a corrupted/overwritten state
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Refuse any non-TLS access to the state bucket.
data "aws_iam_policy_document" "tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket     = aws_s3_bucket.state.id
  policy     = data.aws_iam_policy_document.tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# State locking: Terraform writes a LockID item here for the duration of
# plan/apply, so two concurrent runs cannot corrupt state.
resource "aws_dynamodb_table" "lock" {
  name         = local.lock_table
  billing_mode = "PAY_PER_REQUEST" # no idle cost; pennies at most
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}
