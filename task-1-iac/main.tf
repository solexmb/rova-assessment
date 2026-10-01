locals {
  name = "${var.project}-${var.environment}"

  # The three mandatory tags. Applied via provider default_tags (all taggable
  # resources) and explicitly to things default_tags cannot reach (ASG
  # instances, launch template volumes).
  tags = {
    project     = var.project
    environment = var.environment
    owner       = var.owner
  }

  nat_count = var.enable_nat_gateway ? (var.single_nat_gateway ? 1 : var.az_count) : 0
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)
}
