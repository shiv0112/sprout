variable "project_id" {
  description = "GCP project ID (e.g. sprout-cs5224)"
  type        = string
}

variable "region" {
  description = "GCP region for regional resources"
  type        = string
  default     = "asia-southeast1"
}

variable "zone" {
  description = "GCP zone for the GKE zonal cluster"
  type        = string
  default     = "asia-southeast1-a"
}

variable "environment" {
  description = "Deployment environment suffix (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "billing_account_id" {
  description = "GCP billing account ID for budget alerts. Run: gcloud billing accounts list. Leave empty to skip budget creation."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "GitHub repository (owner/name) for Workload Identity Federation"
  type        = string
  default     = "aryabyte21/sprout"
}
