variable "aws_region" {
  description = "AWS region for the state backend (must match the main stack's backend config)."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name; used in resource names and the 'project' tag."
  type        = string
  default     = "iac-task"
}

variable "environment" {
  description = "Environment name for the 'environment' tag."
  type        = string
  default     = "shared"
}

variable "owner" {
  description = "Owner (person/team) for the 'owner' tag."
  type        = string
}

variable "force_destroy" {
  description = "Allow 'terraform destroy' to delete the state bucket even if it contains (versioned) objects. Leave false until you are tearing everything down."
  type        = bool
  default     = false
}
