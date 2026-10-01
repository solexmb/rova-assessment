variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name. Used in names and the 'project' tag."
  type        = string
  default     = "ha-web"

  validation {
    condition     = can(regex("^[a-z0-9-]{1,16}$", var.project))
    error_message = "project must be 1-16 chars of lowercase letters, digits or hyphens (ALB/TG names have a 32 char limit)."
  }
}

variable "environment" {
  description = "Environment name (dev, staging, prod). Used in names and the 'environment' tag."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z0-9-]{1,10}$", var.environment))
    error_message = "environment must be 1-10 chars of lowercase letters, digits or hyphens."
  }
}

variable "owner" {
  description = "Owner (person or team) for the 'owner' tag. Required, no default."
  type        = string

  validation {
    condition     = length(trimspace(var.owner)) > 0
    error_message = "owner must not be empty."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of Availability Zones to span (>= 2 required for HA)."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "enable_nat_gateway" {
  description = "Create NAT gateway(s) so private instances have outbound internet. Costs ~$0.045/hr each (NOT free tier). The demo app needs no egress, so set false to save money."
  type        = bool
  default     = true
}

variable "single_nat_gateway" {
  description = "true = one shared NAT (cheap, but a single-AZ dependency for egress). false = one NAT per AZ (production-grade, 2-3x cost)."
  type        = bool
  default     = true
}

variable "instance_type" {
  description = "EC2 instance type (x86_64). t3.micro is free-tier eligible on newer accounts; use t2.micro on older ones."
  type        = string
  default     = "t3.micro"
}

variable "asg_min_size" {
  description = "Minimum instances. Keep >= 2 so both AZs always have capacity."
  type        = number
  default     = 2
}

variable "asg_max_size" {
  description = "Maximum instances."
  type        = number
  default     = 4
}

variable "asg_desired_capacity" {
  description = "Initial desired instances (autoscaling manages it afterwards)."
  type        = number
  default     = 2
}

variable "cpu_target_percent" {
  description = "Target average CPU utilisation (%) for target-tracking scaling."
  type        = number
  default     = 50
}
