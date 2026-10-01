terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Partial backend config: bucket/key/region/table are supplied at init time
  # (terraform init -backend-config=backend.hcl) because backend blocks cannot
  # use variables. Locking is provided by the DynamoDB table.
  backend "s3" {}
}
