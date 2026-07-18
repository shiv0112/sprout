terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  name_prefix = "sprout-${var.environment}"
}

# ── APIs ───────────────────────────────────────────────────────────────────────

resource "google_project_service" "apis" {
  for_each = toset([
    "container.googleapis.com",
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "billingbudgets.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}

# ── VPC ────────────────────────────────────────────────────────────────────────

resource "google_compute_network" "vpc" {
  name                    = "${local.name_prefix}-vpc"
  auto_create_subnetworks = false

  depends_on = [google_project_service.apis]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${local.name_prefix}-subnet"
  network       = google_compute_network.vpc.id
  ip_cidr_range = "10.0.0.0/20"
  region        = var.region

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.4.0.0/14"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.8.0.0/20"
  }
}

resource "google_compute_global_address" "ingress_ip" {
  name = "${local.name_prefix}-ingress-ip"

  depends_on = [google_project_service.apis]
}

# ── GKE Standard Zonal Cluster ─────────────────────────────────────────────────

resource "google_container_cluster" "sprout" {
  name     = "${local.name_prefix}-gke"
  location = var.zone

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.subnet.id

  release_channel {
    channel = "REGULAR"
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = "0.0.0.0/0"
      display_name = "all"
    }
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  remove_default_node_pool = true
  initial_node_count       = 1

  deletion_protection = false

  depends_on = [google_project_service.apis]
}

resource "google_container_node_pool" "main" {
  name     = "main-pool"
  cluster  = google_container_cluster.sprout.id
  location = var.zone

  node_count = 1

  node_config {
    machine_type = "e2-standard-2"
    disk_size_gb = 50
    disk_type    = "pd-balanced"

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

resource "google_container_node_pool" "burst" {
  name     = "burst-pool"
  cluster  = google_container_cluster.sprout.id
  location = var.zone

  initial_node_count = 0

  autoscaling {
    min_node_count = 0
    max_node_count = 3
  }

  node_config {
    machine_type = "e2-small"
    disk_size_gb = 30
    disk_type    = "pd-standard"
    spot         = true

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]

    taint {
      key    = "pool"
      value  = "burst"
      effect = "NO_SCHEDULE"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

# ── Artifact Registry ──────────────────────────────────────────────────────────

resource "google_artifact_registry_repository" "sprout" {
  repository_id = "sprout"
  location      = var.region
  format        = "DOCKER"

  depends_on = [google_project_service.apis]
}

# ── GCS Buckets ────────────────────────────────────────────────────────────────

resource "google_storage_bucket" "tools" {
  name          = "${var.project_id}-sprout-tools"
  location      = var.region
  force_destroy = true

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 5
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket" "pg_backups" {
  name          = "${var.project_id}-sprout-pg-backups"
  location      = var.region
  force_destroy = false

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = 14
    }
    action {
      type = "Delete"
    }
  }
}

# ── IAM — Workload Identity ───────────────────────────────────────────────────

resource "google_service_account" "sprout_workload" {
  account_id   = "${local.name_prefix}-workload"
  display_name = "Sprout GKE Workload Identity"
}

resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = google_service_account.sprout_workload.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[sprout/sprout-sa]"
}

resource "google_storage_bucket_iam_member" "tools_admin" {
  bucket = google_storage_bucket.tools.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.sprout_workload.email}"
}

resource "google_storage_bucket_iam_member" "pg_backups_writer" {
  bucket = google_storage_bucket.pg_backups.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.sprout_workload.email}"
}

resource "google_project_iam_member" "workload_ar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.sprout_workload.email}"
}

# ── IAM — CI/CD Service Account ───────────────────────────────────────────────

resource "google_service_account" "github_ci" {
  account_id   = "${local.name_prefix}-github-ci"
  display_name = "GitHub Actions CI/CD"
}

resource "google_project_iam_member" "ci_ar_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.github_ci.email}"
}

resource "google_project_iam_member" "ci_gke_developer" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.github_ci.email}"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
  }

  attribute_condition = "assertion.repository == '${var.github_repository}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "ci_wif" {
  service_account_id = google_service_account.github_ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

# ── Budget Alerts ──────────────────────────────────────────────────────────────

resource "google_billing_budget" "sprout" {
  count = var.billing_account_id != "" ? 1 : 0

  billing_account = var.billing_account_id
  display_name    = "${local.name_prefix}-budget"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = "300"
    }
  }

  threshold_rules {
    threshold_percent = 0.1667
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.8333
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }
}
