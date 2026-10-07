terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.90"
    }
  }

  # Partial backend: pass bucket, key, region, dynamodb_table at init (see README).
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = merge({ Project = var.name, ManagedBy = "terraform" }, var.tags)
  }
}

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}
