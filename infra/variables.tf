variable "name" {
  description = "Prefix for resource names."
  type        = string
  default     = "reference-engine"
}

variable "region" {
  description = "AWS region. It must offer Bedrock with the chosen models."
  type        = string
  default     = "eu-west-2"
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}

# ---- network ----------------------------------------------------------------------------------

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "nat_per_az" {
  description = "One NAT gateway per AZ (resilient) instead of one shared (cheaper)."
  type        = bool
  default     = false
}

variable "alb_allowed_cidrs" {
  description = "Who may reach the internal ALB: your VPN / corporate / transit-gateway ranges."
  type        = list(string)

  validation {
    condition     = length(var.alb_allowed_cidrs) > 0 && !contains(var.alb_allowed_cidrs, "0.0.0.0/0")
    error_message = "List the corporate ranges that may reach the portal; 0.0.0.0/0 is not allowed."
  }
}

# ---- application ------------------------------------------------------------------------------

variable "image_tag" {
  description = "Tag of the image in the ECR repository (tags are immutable: use a version or commit)."
  type        = string
}

variable "app_origin" {
  description = "Public https URL users type, e.g. https://references.example.com (no trailing slash)."
  type        = string

  validation {
    condition     = can(regex("^https://[^/]+$", var.app_origin))
    error_message = "app_origin must be an https URL without a path or trailing slash."
  }
}

variable "certificate_arn" {
  description = "ACM certificate for app_origin's host, in the same region."
  type        = string
}

variable "web_cpu" {
  type    = number
  default = 1024
}

variable "web_memory" {
  type    = number
  default = 2048
}

variable "web_desired_count" {
  type    = number
  default = 2
}

variable "worker_cpu" {
  type    = number
  default = 2048
}

variable "worker_memory" {
  description = "Hard task memory limit. The worker runs Docling on untrusted web content."
  type        = number
  default     = 8192
}

variable "worker_desired_count" {
  type    = number
  default = 1
}

variable "cpu_architecture" {
  description = "Must match the image: X86_64 or ARM64."
  type        = string
  default     = "X86_64"
}

variable "log_retention_days" {
  type    = number
  default = 90
}

variable "extract_model" {
  description = "LiteLLM model id for EXTRACT_MODEL (small), e.g. bedrock/<model-id>."
  type        = string
}

variable "draft_model" {
  description = "LiteLLM model id for DRAFT_MODEL (mid-tier), e.g. bedrock/<model-id>."
  type        = string
}

variable "bedrock_model_arns" {
  description = "Exactly the model (and inference profile) ARNs the app may invoke."
  type        = list(string)

  validation {
    condition     = length(var.bedrock_model_arns) > 0
    error_message = "List at least one Bedrock model ARN."
  }
}

variable "oidc_metadata_url" {
  type = string
}

variable "oidc_client_id" {
  type = string
}

variable "oidc_groups_claim" {
  type    = string
  default = "groups"
}

variable "role_user_groups" {
  description = "Comma-separated IdP group ids that get the user role."
  type        = string
}

variable "role_reviewer_groups" {
  type = string
}

variable "role_admin_groups" {
  type = string
}

variable "graph_client_id" {
  description = "Entra app registration used by the SharePoint crawler (the secret goes in Secrets Manager)."
  type        = string
  default     = ""
}

variable "confluence_email" {
  description = "Confluence Cloud account email (basic auth). Leave empty for a Data Center bearer token."
  type        = string
  default     = ""
}

variable "upload_prefix" {
  description = "S3 prefix of the 'upload' source; the web task may write only here."
  type        = string
  default     = "uploads/"
}

variable "secret_recovery_days" {
  type    = number
  default = 7
}

# ---- database ---------------------------------------------------------------------------------

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage" {
  type    = number
  default = 50
}

variable "db_max_allocated_storage" {
  type    = number
  default = 200
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "db_backup_retention_days" {
  type    = number
  default = 14
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "db_name" {
  type    = string
  default = "refs"
}

variable "db_username" {
  type    = string
  default = "refs"
}

# ---- storage, schedule, shares ----------------------------------------------------------------

variable "bucket_name" {
  description = "Originals bucket. Default: <name>-<account id>-originals."
  type        = string
  default     = null
}

variable "alb_deletion_protection" {
  type    = bool
  default = true
}

variable "crawl_schedule" {
  description = "EventBridge Scheduler expression for the daily crawl."
  type        = string
  default     = "cron(0 3 * * ? *)"
}

variable "crawl_timezone" {
  type    = string
  default = "UTC"
}

variable "datasync_shares" {
  description = <<-EOT
    SMB file shares copied to s3://<bucket>/shares/<name>/. The DataSync agent must already be
    activated (see README). password_secret_arn is a Secrets Manager secret holding the share password
    as plain text; Terraform reads it, so it ends up in state: encrypt and restrict the state bucket.
  EOT
  type = list(object({
    name                = string
    smb_server          = string
    subdomain           = optional(string, "")
    path                = string
    user                = string
    password_secret_arn = string
    agent_arn           = string
    schedule            = optional(string, "cron(0 1 * * ? *)")
  }))
  default = []
}
