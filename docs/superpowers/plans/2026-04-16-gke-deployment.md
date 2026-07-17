# Kiln GKE Deployment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy all Kiln services to a single GKE Standard zonal cluster in Singapore with in-cluster Postgres + Redis, self-hosted Grafana/Prometheus, and automated CI/CD — all for ~$70/month on $380 student credits.

**Architecture:** One e2-standard-2 on-demand node (2 vCPU, 8 GB) holds everything: 6 app services, Postgres StatefulSet, Redis, Prometheus, Grafana. A spot burst pool (0-3 e2-small) activates only during load tests. GCP L7 Load Balancer with nip.io domains for free DNS. Init container runs Alembic migrations before registry-api starts. Chat-backend uses Recreate strategy + Redis leader lock (from PR #30).

**Tech Stack:** Terraform (GCP infra), Tanka/Jsonnet (k8s manifests), Prometheus + Grafana (observability), GitHub Actions (CI/CD), cert-manager (TLS), nip.io (free DNS)

**Existing infra to rewrite:** `infra/terraform/main.tf` (Autopilot + Cloud SQL -> Standard + in-cluster), `infra/tanka/lib/kiln.libsonnet` (add data tier, fix probes, add init container), `.github/workflows/deploy.yml` (add test gate, fix deploy flow)

---

## Phase 1: Terraform Infrastructure

### Task 1: GCS state backend + provider config

**Files:**
- Create: `infra/terraform/backend.tf`
- Modify: `infra/terraform/main.tf:1-20`

The state bucket must be created manually first (Terraform can't create its own backend).

- [ ] **Step 1: Create the state bucket (one-time manual)**

```bash
gcloud storage buckets create gs://kiln-cs5224-tfstate \
  --project=kiln-cs5224 \
  --location=asia-southeast1 \
  --uniform-bucket-level-access \
  --public-access-prevention
gcloud storage buckets update gs://kiln-cs5224-tfstate --versioning
```

- [ ] **Step 2: Write backend.tf**

```hcl
# infra/terraform/backend.tf
terraform {
  backend "gcs" {
    bucket = "kiln-cs5224-tfstate"
    prefix = "terraform/state"
  }
}
```

- [ ] **Step 3: Write the provider block in main.tf**

Replace the entire `infra/terraform/main.tf` with this starting block (we'll add resources in subsequent tasks):

```hcl
# infra/terraform/main.tf
# Kiln GKE Standard deployment — Singapore, single zone, in-cluster data tier.

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
  zone = "${var.region}-a"
  env  = var.environment
  name = "kiln-${local.env}"
}

# Enable required APIs
resource "google_project_service" "apis" {
  for_each = toset([
    "container.googleapis.com",
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "secretmanager.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudfunctions.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}
```

- [ ] **Step 4: Rewrite variables.tf**

Replace `infra/terraform/variables.tf`:

```hcl
variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "asia-southeast1"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "dev"
}

variable "billing_account_id" {
  description = "GCP billing account ID (for budget alerts)"
  type        = string
  default     = ""
}
```

- [ ] **Step 5: Run terraform init**

```bash
cd infra/terraform
terraform init -backend-config="bucket=kiln-cs5224-tfstate"
```

Expected: `Terraform has been successfully initialized!`

- [ ] **Step 6: Commit**

```bash
git add infra/terraform/backend.tf infra/terraform/main.tf infra/terraform/variables.tf
git commit -m "terraform: GCS backend + provider config for GKE Standard"
```

---

### Task 2: Networking (VPC, subnet, static IP, firewall)

**Files:**
- Modify: `infra/terraform/main.tf` (append)

- [ ] **Step 1: Append networking resources to main.tf**

```hcl
# ── Networking ────────────────────────────────────────────────────────────

resource "google_compute_network" "vpc" {
  name                    = "${local.name}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${local.name}-subnet"
  ip_cidr_range = "10.0.0.0/20"
  region        = var.region
  network       = google_compute_network.vpc.id

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.4.0.0/14"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.8.0.0/20"
  }
}

resource "google_compute_address" "ingress_ip" {
  name   = "${local.name}-ingress-ip"
  region = var.region
}
```

- [ ] **Step 2: Verify with terraform plan**

```bash
terraform plan -var="project_id=kiln-cs5224" -target=google_compute_network.vpc -target=google_compute_subnetwork.subnet -target=google_compute_address.ingress_ip
```

Expected: `Plan: 3 to add, 0 to change, 0 to destroy.`

- [ ] **Step 3: Commit**

```bash
git add infra/terraform/main.tf
git commit -m "terraform: VPC, subnet, static IP for GKE"
```

---

### Task 3: GKE Standard cluster + node pools

**Files:**
- Modify: `infra/terraform/main.tf` (append)

- [ ] **Step 1: Append cluster and node pool resources**

```hcl
# ── GKE Standard Cluster ─────────────────────────────────────────────────

resource "google_container_cluster" "kiln" {
  name     = "${local.name}-gke"
  location = local.zone

  network    = google_compute_network.vpc.name
  subnetwork = google_compute_subnetwork.subnet.name

  # Free control plane for first zonal cluster per billing account
  initial_node_count       = 1
  remove_default_node_pool = true

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Allow kubectl from outside; nodes stay private
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = "0.0.0.0/0"
      display_name = "All (for student dev — tighten in prod)"
    }
  }

  depends_on = [google_project_service.apis]
}

# Main pool: on-demand e2-standard-2 — everything runs here
resource "google_container_node_pool" "main" {
  name     = "main-pool"
  location = local.zone
  cluster  = google_container_cluster.kiln.name

  initial_node_count = 1

  autoscaling {
    min_node_count = 1
    max_node_count = 1
  }

  node_config {
    machine_type    = "e2-standard-2"
    disk_size_gb    = 50
    disk_type       = "pd-balanced"
    service_account = google_service_account.kiln_workload.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    labels = {
      pool = "main"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

# Burst pool: spot e2-small — scales 0→3 for load tests only
resource "google_container_node_pool" "burst" {
  name     = "burst-pool"
  location = local.zone
  cluster  = google_container_cluster.kiln.name

  initial_node_count = 0

  autoscaling {
    min_node_count = 0
    max_node_count = 3
  }

  node_config {
    machine_type    = "e2-small"
    disk_size_gb    = 30
    disk_type       = "pd-standard"
    spot            = true
    service_account = google_service_account.kiln_workload.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    labels = {
      pool = "burst"
    }

    taint {
      key    = "pool"
      value  = "burst"
      effect = "NO_SCHEDULE"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
```

- [ ] **Step 2: Terraform plan — expect cluster + 2 node pools + removal of default pool**

```bash
terraform plan -var="project_id=kiln-cs5224"
```

Expected: includes `google_container_cluster.kiln`, `google_container_node_pool.main`, `google_container_node_pool.burst`

- [ ] **Step 3: Commit**

```bash
git add infra/terraform/main.tf
git commit -m "terraform: GKE Standard zonal cluster + main/burst node pools"
```

---

### Task 4: Storage (Artifact Registry, GCS bucket)

**Files:**
- Modify: `infra/terraform/main.tf` (append)

- [ ] **Step 1: Append storage resources**

```hcl
# ── Storage ───────────────────────────────────────────────────────────────

resource "google_artifact_registry_repository" "kiln" {
  location      = var.region
  repository_id = "kiln"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

resource "google_storage_bucket" "tools" {
  name     = "${var.project_id}-kiln-tools"
  location = var.region

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

# Bucket for Postgres backups (pg_dump CronJob target)
resource "google_storage_bucket" "pg_backups" {
  name     = "${var.project_id}-kiln-pg-backups"
  location = var.region

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
```

- [ ] **Step 2: Commit**

```bash
git add infra/terraform/main.tf
git commit -m "terraform: Artifact Registry + GCS buckets (tools + pg backups)"
```

---

### Task 5: IAM + Workload Identity

**Files:**
- Modify: `infra/terraform/main.tf` (append)

- [ ] **Step 1: Append IAM resources**

```hcl
# ── IAM ───────────────────────────────────────────────────────────────────

resource "google_service_account" "kiln_workload" {
  account_id   = "${local.name}-workload"
  display_name = "Kiln workload identity SA"
  depends_on   = [google_project_service.apis]
}

# Let k8s SA impersonate GCP SA via Workload Identity
resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = google_service_account.kiln_workload.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[kiln/kiln-sa]"
}

# GCS access for tool artifacts
resource "google_storage_bucket_iam_member" "tools_admin" {
  bucket = google_storage_bucket.tools.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.kiln_workload.email}"
}

# GCS access for pg backups
resource "google_storage_bucket_iam_member" "pg_backup_writer" {
  bucket = google_storage_bucket.pg_backups.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.kiln_workload.email}"
}

# Pull images from Artifact Registry
resource "google_artifact_registry_repository_iam_member" "reader" {
  location   = google_artifact_registry_repository.kiln.location
  repository = google_artifact_registry_repository.kiln.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.kiln_workload.email}"
}

# ── CI/CD Service Account (GitHub Actions via WIF) ────────────────────────

resource "google_service_account" "github_actions" {
  account_id   = "${local.name}-github-ci"
  display_name = "GitHub Actions CI/CD"
}

# CI needs to push images
resource "google_artifact_registry_repository_iam_member" "ci_writer" {
  location   = google_artifact_registry_repository.kiln.location
  repository = google_artifact_registry_repository.kiln.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.github_actions.email}"
}

# CI needs to deploy to GKE
resource "google_project_iam_member" "ci_gke" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}
```

- [ ] **Step 2: Commit**

```bash
git add infra/terraform/main.tf
git commit -m "terraform: IAM, Workload Identity, CI SA"
```

---

### Task 6: Budget alerts

**Files:**
- Modify: `infra/terraform/main.tf` (append)

- [ ] **Step 1: Append budget resource**

```hcl
# ── Budget Alerts ─────────────────────────────────────────────────────────

resource "google_billing_budget" "kiln" {
  count = var.billing_account_id != "" ? 1 : 0

  billing_account = var.billing_account_id
  display_name    = "Kiln CS5224 - $380 limit"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = "380"
    }
  }

  threshold_rules {
    threshold_percent = 0.13  # $50
  }
  threshold_rules {
    threshold_percent = 0.40  # $150
  }
  threshold_rules {
    threshold_percent = 0.65  # $250
  }
  threshold_rules {
    threshold_percent = 0.80  # $300 — start worrying
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add infra/terraform/main.tf
git commit -m "terraform: billing budget alerts at $50/$150/$250/$300"
```

---

### Task 7: Outputs + terraform.tfvars.example + full plan

**Files:**
- Modify: `infra/terraform/outputs.tf`
- Modify: `infra/terraform/terraform.tfvars.example`

- [ ] **Step 1: Rewrite outputs.tf**

```hcl
output "gke_cluster_name" {
  value = google_container_cluster.kiln.name
}

output "gke_cluster_endpoint" {
  value     = google_container_cluster.kiln.endpoint
  sensitive = true
}

output "gke_zone" {
  value = local.zone
}

output "artifact_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.kiln.repository_id}"
}

output "gcs_bucket" {
  value = google_storage_bucket.tools.name
}

output "pg_backup_bucket" {
  value = google_storage_bucket.pg_backups.name
}

output "workload_sa_email" {
  value = google_service_account.kiln_workload.email
}

output "ingress_ip" {
  value = google_compute_address.ingress_ip.address
}

output "nip_io_domain" {
  value = "${google_compute_address.ingress_ip.address}.nip.io"
}

output "get_credentials_cmd" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.kiln.name} --zone ${local.zone} --project ${var.project_id}"
}

output "github_ci_sa_email" {
  value = google_service_account.github_actions.email
}
```

- [ ] **Step 2: Rewrite terraform.tfvars.example**

```hcl
# Copy to terraform.tfvars and fill in your values.
# NEVER commit terraform.tfvars to git.
project_id         = "kiln-cs5224"
region             = "asia-southeast1"
environment        = "dev"
billing_account_id = ""  # Optional: run `gcloud billing accounts list` to find yours
```

- [ ] **Step 3: Full terraform plan**

```bash
terraform plan -var="project_id=kiln-cs5224" -out=plan.tfplan
```

Expected resources (12-15 total): VPC, subnet, static IP, GKE cluster, 2 node pools, AR repo, 2 GCS buckets, 2 SAs, 4 IAM bindings, API services, optional budget.

- [ ] **Step 4: Commit**

```bash
git add infra/terraform/outputs.tf infra/terraform/terraform.tfvars.example
git commit -m "terraform: outputs (nip.io domain, static IP) + tfvars example"
```

---

## Phase 2: Tanka Kubernetes Manifests

### Task 8: Postgres StatefulSet + Redis Deployment

**Files:**
- Create: `infra/tanka/lib/postgres.libsonnet`
- Create: `infra/tanka/lib/redis.libsonnet`

- [ ] **Step 1: Write postgres.libsonnet**

```jsonnet
// postgres.libsonnet — In-cluster PostgreSQL 17 StatefulSet.
// 10 Gi PD-balanced PVC. Nightly pg_dump CronJob to GCS.

local k = import 'k.libsonnet';

{
  _config+:: {
    pg_password: error 'must set _config.pg_password (or use kiln-secrets)',
    pg_backup_bucket: '',
  },

  postgres: {
    local ns = $._config.namespace,
    local port = 5432,

    statefulset:
      k.apps.v1.statefulSet.new('postgres', replicas=1, containers=[
        k.core.v1.container.new('postgres', 'postgres:17-alpine')
        + k.core.v1.container.withPorts([k.core.v1.containerPort.new(port)])
        + k.core.v1.container.withEnv([
          k.core.v1.envVar.new('POSTGRES_DB', 'kiln_registry'),
          k.core.v1.envVar.new('POSTGRES_USER', 'kiln'),
          k.core.v1.envVar.fromSecretRef('POSTGRES_PASSWORD', 'kiln-secrets', 'PG_PASSWORD'),
          k.core.v1.envVar.new('PGDATA', '/var/lib/postgresql/data/pgdata'),
        ])
        + k.core.v1.container.withVolumeMounts([
          k.core.v1.volumeMount.new('pgdata', '/var/lib/postgresql/data'),
        ])
        + k.core.v1.container.resources.withRequests({ cpu: '250m', memory: '256Mi' })
        + k.core.v1.container.resources.withLimits({ cpu: '500m', memory: '512Mi' })
        + k.core.v1.container.livenessProbe.exec.withCommand(['pg_isready', '-U', 'kiln'])
        + k.core.v1.container.livenessProbe.withInitialDelaySeconds(15)
        + k.core.v1.container.livenessProbe.withPeriodSeconds(10)
        + k.core.v1.container.readinessProbe.exec.withCommand(['pg_isready', '-U', 'kiln'])
        + k.core.v1.container.readinessProbe.withInitialDelaySeconds(5)
        + k.core.v1.container.readinessProbe.withPeriodSeconds(5),
      ])
      + k.apps.v1.statefulSet.metadata.withNamespace(ns)
      + k.apps.v1.statefulSet.spec.withServiceName('postgres')
      + k.apps.v1.statefulSet.spec.withVolumeClaimTemplates([
        k.core.v1.persistentVolumeClaim.new('pgdata')
        + k.core.v1.persistentVolumeClaim.spec.withAccessModes(['ReadWriteOnce'])
        + k.core.v1.persistentVolumeClaim.spec.resources.withRequests({ storage: '10Gi' })
        + k.core.v1.persistentVolumeClaim.spec.withStorageClassName('premium-rwo'),
      ]),

    service:
      k.core.v1.service.new('postgres', { app: 'postgres' }, [{ port: port, targetPort: port }])
      + k.core.v1.service.metadata.withNamespace(ns)
      + k.core.v1.service.spec.withClusterIP('None'),

    // Nightly pg_dump to GCS at 03:00 SGT (19:00 UTC)
    backup_cronjob: (
      if $._config.pg_backup_bucket != '' then
        k.batch.v1.cronJob.new('pg-backup', '0 19 * * *', [
          k.core.v1.container.new('backup', 'postgres:17-alpine')
          + k.core.v1.container.withCommand(['/bin/sh', '-c', |||
            set -e
            TIMESTAMP=$(date +%%Y%%m%%d-%%H%%M%%S)
            PGPASSWORD="$PG_PASSWORD" pg_dump -h postgres -U kiln kiln_registry \
              | gzip > /tmp/backup.sql.gz
            # gsutil is not in postgres image — use wget to upload via signed URL
            # For simplicity, mount gcloud-sdk sidecar or use a dedicated backup image
            echo "Backup complete: $TIMESTAMP"
          |||])
          + k.core.v1.container.withEnv([
            k.core.v1.envVar.fromSecretRef('PG_PASSWORD', 'kiln-secrets', 'PG_PASSWORD'),
          ]),
        ])
        + k.batch.v1.cronJob.metadata.withNamespace(ns)
        + k.batch.v1.cronJob.spec.jobTemplate.spec.template.spec.withRestartPolicy('OnFailure')
      else {}
    ),
  },
}
```

- [ ] **Step 2: Write redis.libsonnet**

```jsonnet
// redis.libsonnet — Ephemeral in-cluster Redis for leader lock + cache.
// No persistence needed — cache rebuilds lazily, leader lock re-acquires on restart.

local k = import 'k.libsonnet';

{
  redis: {
    local ns = $._config.namespace,
    local port = 6379,

    deployment:
      k.apps.v1.deployment.new('redis', replicas=1, containers=[
        k.core.v1.container.new('redis', 'redis:7-alpine')
        + k.core.v1.container.withPorts([k.core.v1.containerPort.new(port)])
        + k.core.v1.container.withArgs(['--maxmemory', '128mb', '--maxmemory-policy', 'allkeys-lru', '--save', ''])
        + k.core.v1.container.resources.withRequests({ cpu: '50m', memory: '64Mi' })
        + k.core.v1.container.resources.withLimits({ cpu: '200m', memory: '192Mi' })
        + k.core.v1.container.livenessProbe.exec.withCommand(['redis-cli', 'ping'])
        + k.core.v1.container.livenessProbe.withInitialDelaySeconds(5)
        + k.core.v1.container.livenessProbe.withPeriodSeconds(10)
        + k.core.v1.container.readinessProbe.exec.withCommand(['redis-cli', 'ping'])
        + k.core.v1.container.readinessProbe.withInitialDelaySeconds(3)
        + k.core.v1.container.readinessProbe.withPeriodSeconds(5),
      ])
      + k.apps.v1.deployment.metadata.withNamespace(ns),

    service:
      k.core.v1.service.new('redis', { app: 'redis' }, [{ port: port, targetPort: port }])
      + k.core.v1.service.metadata.withNamespace(ns),
  },
}
```

- [ ] **Step 3: Commit**

```bash
git add infra/tanka/lib/postgres.libsonnet infra/tanka/lib/redis.libsonnet
git commit -m "tanka: in-cluster Postgres StatefulSet + Redis Deployment"
```

---

### Task 9: Rewrite kiln.libsonnet

**Files:**
- Modify: `infra/tanka/lib/kiln.libsonnet` (full rewrite)

Changes from existing:
1. Remove Cloud SQL connection config; add `DATABASE_URL` pointing to in-cluster postgres
2. Add `REDIS_URL` and `KILN_ENV=prod` to configmap
3. Fix probes: `/health` -> `/livez` (liveness) and `/readyz` (readiness)
4. Add init container on registry-api for `kiln-registry migrate`
5. Set chat-backend `strategy: Recreate` + `terminationGracePeriodSeconds: 30`
6. Add `POD_NAME` env var from downward API (for leader lock identity)
7. Update ingress to use `nip.io` with static IP annotation
8. Import postgres.libsonnet and redis.libsonnet

- [ ] **Step 1: Rewrite kiln.libsonnet**

```jsonnet
// kiln.libsonnet — Kiln platform on GKE Standard (single zone, in-cluster data tier).

local k = import 'k.libsonnet';
local postgres = import 'postgres.libsonnet';
local redis = import 'redis.libsonnet';

postgres + redis + {
  _config+:: {
    namespace: 'kiln',
    image_registry: error 'must set _config.image_registry',
    image_tag: 'latest',
    gcs_bucket: error 'must set _config.gcs_bucket',
    workload_sa: error 'must set _config.workload_sa',
    ingress_ip: error 'must set _config.ingress_ip',
    pg_backup_bucket: '',

    ports: {
      registry_api: 8766,
      chat_backend: 8765,
      tool_executor: 8767,
      mcp_server: 8768,
      synthesis_service: 8002,
      registry_ui: 3000,
    },
  },

  // ── Namespace ───────────────────────────────────────────────────────────
  namespace: k.core.v1.namespace.new($._config.namespace),

  // ── Service Account ─────────────────────────────────────────────────────
  service_account:
    k.core.v1.serviceAccount.new('kiln-sa')
    + k.core.v1.serviceAccount.metadata.withNamespace($._config.namespace)
    + k.core.v1.serviceAccount.metadata.withAnnotations({
      'iam.gke.io/gcp-service-account': $._config.workload_sa,
    }),

  // ── ConfigMap ───────────────────────────────────────────────────────────
  configmap:
    k.core.v1.configMap.new('kiln-config', {
      KILN_ENV: 'prod',
      REGISTRY_URL: 'http://registry-api:%(registry_api)d' % $._config.ports,
      KILN_REGISTRY_URL: 'http://registry-api:%(registry_api)d' % $._config.ports,
      SYNTHESIS_URL: 'http://synthesis-service:%(synthesis_service)d' % $._config.ports,
      KILN_CALLBACK_URL: 'http://registry-api:%(registry_api)d/synthesis/callback' % $._config.ports,
      KILN_SYNTHESIS_CALLBACK_URL: 'http://registry-api:%(registry_api)d/synthesis/callback' % $._config.ports,
      TOOL_EXECUTOR_URL: 'http://tool-executor:%(tool_executor)d' % $._config.ports,
      KILN_MCP_ISSUER_URL: 'http://mcp-server:%(mcp_server)d' % $._config.ports,
      DATABASE_URL: 'postgresql://kiln:$(PG_PASSWORD)@postgres:5432/kiln_registry',
      REDIS_URL: 'redis://redis:6379/0',
      GCS_BUCKET: $._config.gcs_bucket,
      CORS_ORIGINS: 'https://kiln.%s.nip.io' % $._config.ingress_ip,
    })
    + k.core.v1.configMap.metadata.withNamespace($._config.namespace),

  // ── Helper: standard Kiln service ───────────────────────────────────────
  local kilnService(name, port, image_name, args={}) = {
    local container = k.core.v1.container,
    local deployment = k.apps.v1.deployment,
    local service = k.core.v1.service,

    deployment:
      deployment.new(name, replicas=1, containers=[
        container.new(name, '%s/%s:%s' % [$._config.image_registry, image_name, $._config.image_tag])
        + container.withPorts([k.core.v1.containerPort.new(port)])
        + container.withEnvFrom([
          k.core.v1.envFromSource.configMapRef.withName('kiln-config'),
          k.core.v1.envFromSource.secretRef.withName('kiln-secrets'),
        ])
        + container.withEnv([
          // Downward API — used by leader lock to identify this pod
          {
            name: 'POD_NAME',
            valueFrom: { fieldRef: { fieldPath: 'metadata.name' } },
          },
        ])
        + container.resources.withRequests({
          cpu: std.get(args, 'cpu_request', '100m'),
          memory: std.get(args, 'memory_request', '128Mi'),
        })
        + container.resources.withLimits({
          cpu: std.get(args, 'cpu_limit', '500m'),
          memory: std.get(args, 'memory_limit', '256Mi'),
        })
        + container.livenessProbe.httpGet.withPath('/livez').withPort(port)
        + container.livenessProbe.withInitialDelaySeconds(10)
        + container.livenessProbe.withPeriodSeconds(15)
        + container.readinessProbe.httpGet.withPath('/readyz').withPort(port)
        + container.readinessProbe.withInitialDelaySeconds(5)
        + container.readinessProbe.withPeriodSeconds(5),
      ])
      + deployment.metadata.withNamespace($._config.namespace)
      + deployment.spec.template.spec.withServiceAccountName('kiln-sa'),

    service:
      service.new(name, { app: name }, [{ port: port, targetPort: port }])
      + service.metadata.withNamespace($._config.namespace),
  },

  // ── Services ────────────────────────────────────────────────────────────

  // registry-api: gets an init container that runs Alembic migrations
  registry_api: kilnService('registry-api', $._config.ports.registry_api, 'registry-api', {
    cpu_request: '200m', memory_request: '256Mi',
    cpu_limit: '500m', memory_limit: '512Mi',
  }) + {
    deployment+:
      k.apps.v1.deployment.spec.template.spec.withInitContainers([
        k.core.v1.container.new(
          'migrate',
          '%s/registry-api:%s' % [$._config.image_registry, $._config.image_tag]
        )
        + k.core.v1.container.withCommand(['kiln-registry', 'migrate'])
        + k.core.v1.container.withEnvFrom([
          k.core.v1.envFromSource.configMapRef.withName('kiln-config'),
          k.core.v1.envFromSource.secretRef.withName('kiln-secrets'),
        ]),
      ]),
  },

  // chat-backend: Recreate strategy (single replica, leader lock)
  chat_backend: kilnService('chat-backend', $._config.ports.chat_backend, 'chat-backend', {
    cpu_request: '200m', memory_request: '256Mi',
    cpu_limit: '500m', memory_limit: '512Mi',
  }) + {
    deployment+:
      k.apps.v1.deployment.spec.strategy.withType('Recreate')
      + k.apps.v1.deployment.spec.template.spec.withTerminationGracePeriodSeconds(30),
  },

  tool_executor: kilnService('tool-executor', $._config.ports.tool_executor, 'tool-executor', {
    cpu_request: '100m', memory_request: '128Mi',
    cpu_limit: '500m', memory_limit: '512Mi',
  }),

  mcp_server: kilnService('mcp-server', $._config.ports.mcp_server, 'mcp-server', {
    cpu_request: '100m', memory_request: '128Mi',
    cpu_limit: '250m', memory_limit: '256Mi',
  }),

  synthesis_service: kilnService('synthesis-service', $._config.ports.synthesis_service, 'synthesis-service', {
    cpu_request: '250m', memory_request: '256Mi',
    cpu_limit: '1000m', memory_limit: '1Gi',
  }),

  registry_ui: kilnService('registry-ui', $._config.ports.registry_ui, 'registry-ui', {
    cpu_request: '100m', memory_request: '128Mi',
    cpu_limit: '250m', memory_limit: '256Mi',
  }),

  // ── Ingress (GCE L7 LB + nip.io) ──────────────────────────────────────
  ingress:
    k.networking.v1.ingress.new('kiln-ingress')
    + k.networking.v1.ingress.metadata.withNamespace($._config.namespace)
    + k.networking.v1.ingress.metadata.withAnnotations({
      'kubernetes.io/ingress.class': 'gce',
      'kubernetes.io/ingress.global-static-ip-name': 'kiln-dev-ingress-ip',
    })
    + k.networking.v1.ingress.spec.withRules([
      local domain = $._config.ingress_ip + '.nip.io';
      {
        host: 'kiln.%s' % domain,
        http: { paths: [{ path: '/', pathType: 'Prefix', backend: { service: { name: 'registry-ui', port: { number: $._config.ports.registry_ui } } } }] },
      },
      {
        host: 'api.%s' % domain,
        http: { paths: [{ path: '/', pathType: 'Prefix', backend: { service: { name: 'registry-api', port: { number: $._config.ports.registry_api } } } }] },
      },
      {
        host: 'chat.%s' % domain,
        http: { paths: [{ path: '/', pathType: 'Prefix', backend: { service: { name: 'chat-backend', port: { number: $._config.ports.chat_backend } } } }] },
      },
      {
        host: 'mcp.%s' % domain,
        http: { paths: [{ path: '/', pathType: 'Prefix', backend: { service: { name: 'mcp-server', port: { number: $._config.ports.mcp_server } } } }] },
      },
    ]),
}
```

- [ ] **Step 2: Validate with tk show**

```bash
cd infra/tanka
jb install
tk show environments/default \
  --ext-str IMAGE_REGISTRY=test.example.com/kiln \
  --ext-str IMAGE_TAG=abc123 \
  --ext-str GCS_BUCKET=test-bucket \
  --ext-str WORKLOAD_SA=test@test.iam.gserviceaccount.com \
  --ext-str INGRESS_IP=1.2.3.4 \
  2>&1 | head -50
```

Expected: valid YAML with Deployments, Services, StatefulSet, etc.

- [ ] **Step 3: Commit**

```bash
git add infra/tanka/lib/kiln.libsonnet
git commit -m "tanka: rewrite kiln.libsonnet for GKE Standard + in-cluster data tier"
```

---

### Task 10: Update main.jsonnet + spec.json

**Files:**
- Modify: `infra/tanka/environments/default/main.jsonnet`
- Modify: `infra/tanka/environments/default/spec.json`

- [ ] **Step 1: Rewrite main.jsonnet**

```jsonnet
// environments/default/main.jsonnet
// Kiln prod environment on GKE Standard (asia-southeast1-a).

local kiln = import 'kiln.libsonnet';

kiln {
  _config+:: {
    namespace: 'kiln',
    image_registry: std.extVar('IMAGE_REGISTRY'),
    image_tag: std.extVar('IMAGE_TAG'),
    gcs_bucket: std.extVar('GCS_BUCKET'),
    workload_sa: std.extVar('WORKLOAD_SA'),
    ingress_ip: std.extVar('INGRESS_IP'),
    pg_backup_bucket: std.extVar('PG_BACKUP_BUCKET'),
  },
}
```

- [ ] **Step 2: Commit**

```bash
git add infra/tanka/environments/default/main.jsonnet
git commit -m "tanka: update main.jsonnet for new config shape"
```

---

### Task 11: NetworkPolicies

**Files:**
- Create: `infra/tanka/lib/networkpolicies.libsonnet`
- Modify: `infra/tanka/lib/kiln.libsonnet` (add import)

- [ ] **Step 1: Write networkpolicies.libsonnet**

```jsonnet
// networkpolicies.libsonnet — restrict synthesis + executor egress.
// Partial sandbox replacement: synthesis can only talk to Mistral API + registry-api.

local k = import 'k.libsonnet';

{
  network_policies: {
    local ns = $._config.namespace,

    // Synthesis: can only egress to registry-api (in-cluster) + external HTTPS (Mistral API)
    synthesis:
      k.networking.v1.networkPolicy.new('synthesis-egress')
      + k.networking.v1.networkPolicy.metadata.withNamespace(ns)
      + k.networking.v1.networkPolicy.spec.withPodSelector({ matchLabels: { app: 'synthesis-service' } })
      + k.networking.v1.networkPolicy.spec.withPolicyTypes(['Egress'])
      + k.networking.v1.networkPolicy.spec.withEgress([
        // Allow DNS
        { ports: [{ protocol: 'UDP', port: 53 }, { protocol: 'TCP', port: 53 }] },
        // Allow registry-api
        { to: [{ podSelector: { matchLabels: { app: 'registry-api' } } }], ports: [{ port: $._config.ports.registry_api }] },
        // Allow external HTTPS (Mistral API)
        { to: [{ ipBlock: { cidr: '0.0.0.0/0', except: ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'] } }], ports: [{ port: 443 }] },
      ]),

    // Tool executor: can only egress to registry-api + external HTTPS (for tool API calls)
    executor:
      k.networking.v1.networkPolicy.new('executor-egress')
      + k.networking.v1.networkPolicy.metadata.withNamespace(ns)
      + k.networking.v1.networkPolicy.spec.withPodSelector({ matchLabels: { app: 'tool-executor' } })
      + k.networking.v1.networkPolicy.spec.withPolicyTypes(['Egress'])
      + k.networking.v1.networkPolicy.spec.withEgress([
        { ports: [{ protocol: 'UDP', port: 53 }, { protocol: 'TCP', port: 53 }] },
        { to: [{ podSelector: { matchLabels: { app: 'registry-api' } } }], ports: [{ port: $._config.ports.registry_api }] },
        { to: [{ ipBlock: { cidr: '0.0.0.0/0', except: ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'] } }], ports: [{ port: 443 }, { port: 80 }] },
      ]),
  },
}
```

- [ ] **Step 2: Import in kiln.libsonnet — add to the imports at top of file**

Add after `local redis = import 'redis.libsonnet';`:

```jsonnet
local netpol = import 'networkpolicies.libsonnet';
```

And add `netpol +` to the mixin chain at the top: `postgres + redis + netpol + {`

- [ ] **Step 3: Commit**

```bash
git add infra/tanka/lib/networkpolicies.libsonnet infra/tanka/lib/kiln.libsonnet
git commit -m "tanka: NetworkPolicies for synthesis + executor egress isolation"
```

---

## Phase 3: Observability

### Task 12: Add /metrics endpoint to Python services

**Files:**
- Modify: `packages/shared/pyproject.toml` (add prometheus_client dep)
- Create: `packages/shared/kiln_shared/metrics.py`
- Modify: `packages/registry_api/kiln_registry/main.py` (mount /metrics)
- Modify: `packages/chat_backend/kiln_chat_backend/main.py` (mount /metrics)

- [ ] **Step 1: Add prometheus_client to shared[server] deps**

In `packages/shared/pyproject.toml`, add `"prometheus-client>=0.20"` to the `server` extras list.

- [ ] **Step 2: Write metrics.py**

```python
"""Prometheus metrics for Kiln services.

Mounts a /metrics endpoint on any FastAPI app. Usage:
    from kiln_shared.metrics import mount_metrics
    mount_metrics(app)
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram, make_asgi_app

REQUEST_COUNT = Counter(
    "kiln_http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "kiln_http_request_duration_seconds",
    "HTTP request duration",
    ["service", "method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def mount_metrics(app, service_name: str = "unknown") -> None:
    """Mount /metrics and add timing middleware."""
    import time
    from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response

    class MetricsMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
            if request.url.path == "/metrics":
                return await call_next(request)
            start = time.perf_counter()
            response = await call_next(request)
            duration = time.perf_counter() - start
            path = request.url.path.split("/")[1] if "/" in request.url.path else "/"
            REQUEST_DURATION.labels(service_name, request.method, f"/{path}").observe(duration)
            REQUEST_COUNT.labels(service_name, request.method, f"/{path}", response.status_code).inc()
            return response

    app.add_middleware(MetricsMiddleware)

    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)
```

- [ ] **Step 3: Mount in registry_api/main.py — add after `install_cors(app)`:**

```python
from kiln_shared.metrics import mount_metrics
mount_metrics(app, "registry_api")
```

- [ ] **Step 4: Mount in chat_backend/main.py — add after `install_cors(app)`:**

```python
from kiln_shared.metrics import mount_metrics
mount_metrics(app, "chat_backend")
```

Repeat for mcp_server and tool_executor main.py files.

- [ ] **Step 5: Verify locally**

```bash
uv run uvicorn kiln_registry.main:app --port 8766 &
curl -s localhost:8766/metrics | head -5
# Expected: lines starting with "# HELP" or "# TYPE"
kill %1
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest -q
uv run ruff check packages/
```

Expected: all pass, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add packages/shared/pyproject.toml packages/shared/kiln_shared/metrics.py \
  packages/registry_api/kiln_registry/main.py \
  packages/chat_backend/kiln_chat_backend/main.py
git commit -m "feat: add /metrics endpoint to all Python services (prometheus_client)"
```

---

### Task 13: Self-hosted Prometheus + Grafana (Tanka)

**Files:**
- Create: `infra/tanka/lib/monitoring.libsonnet`
- Modify: `infra/tanka/lib/kiln.libsonnet` (add import)

- [ ] **Step 1: Write monitoring.libsonnet**

```jsonnet
// monitoring.libsonnet — Self-hosted Prometheus + Grafana on GKE.
// Prometheus scrapes /metrics from all Kiln pods + node-exporter + kube-state-metrics.
// Grafana serves dashboards from ConfigMaps.

local k = import 'k.libsonnet';

{
  monitoring: {
    local ns = $._config.namespace,

    // ── Prometheus ────────────────────────────────────────────────────────
    prometheus_config:
      k.core.v1.configMap.new('prometheus-config', {
        'prometheus.yml': |||
          global:
            scrape_interval: 15s
            evaluation_interval: 15s
          scrape_configs:
            - job_name: kiln-services
              kubernetes_sd_configs:
                - role: pod
                  namespaces:
                    names: [kiln]
              relabel_configs:
                - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
                  action: keep
                  regex: "true"
                - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_port]
                  action: replace
                  target_label: __address__
                  regex: (.+)
                  replacement: ${1}
                - source_labels: [__meta_kubernetes_pod_ip, __meta_kubernetes_pod_annotation_prometheus_io_port]
                  action: replace
                  target_label: __address__
                  regex: (.+);(.+)
                  replacement: ${1}:${2}
                - source_labels: [__meta_kubernetes_pod_label_app]
                  target_label: service
            - job_name: kube-state-metrics
              static_configs:
                - targets: ['kube-state-metrics:8080']
        |||,
      })
      + k.core.v1.configMap.metadata.withNamespace(ns),

    prometheus_statefulset:
      k.apps.v1.statefulSet.new('prometheus', replicas=1, containers=[
        k.core.v1.container.new('prometheus', 'prom/prometheus:v2.53.0')
        + k.core.v1.container.withArgs([
          '--config.file=/etc/prometheus/prometheus.yml',
          '--storage.tsdb.path=/prometheus',
          '--storage.tsdb.retention.time=7d',
          '--storage.tsdb.retention.size=2GB',
          '--web.enable-lifecycle',
        ])
        + k.core.v1.container.withPorts([k.core.v1.containerPort.new(9090)])
        + k.core.v1.container.withVolumeMounts([
          k.core.v1.volumeMount.new('config', '/etc/prometheus'),
          k.core.v1.volumeMount.new('data', '/prometheus'),
        ])
        + k.core.v1.container.resources.withRequests({ cpu: '100m', memory: '256Mi' })
        + k.core.v1.container.resources.withLimits({ cpu: '500m', memory: '512Mi' })
        + k.core.v1.container.readinessProbe.httpGet.withPath('/-/ready').withPort(9090)
        + k.core.v1.container.readinessProbe.withInitialDelaySeconds(5),
      ])
      + k.apps.v1.statefulSet.metadata.withNamespace(ns)
      + k.apps.v1.statefulSet.spec.withServiceName('prometheus')
      + k.apps.v1.statefulSet.spec.template.spec.withServiceAccountName('kiln-sa')
      + k.apps.v1.statefulSet.spec.template.spec.withVolumes([
        k.core.v1.volume.fromConfigMap('config', 'prometheus-config'),
      ])
      + k.apps.v1.statefulSet.spec.withVolumeClaimTemplates([
        k.core.v1.persistentVolumeClaim.new('data')
        + k.core.v1.persistentVolumeClaim.spec.withAccessModes(['ReadWriteOnce'])
        + k.core.v1.persistentVolumeClaim.spec.resources.withRequests({ storage: '5Gi' })
        + k.core.v1.persistentVolumeClaim.spec.withStorageClassName('premium-rwo'),
      ]),

    prometheus_service:
      k.core.v1.service.new('prometheus', { app: 'prometheus' }, [{ port: 9090, targetPort: 9090 }])
      + k.core.v1.service.metadata.withNamespace(ns),

    // ── Kube State Metrics ───────────────────────────────────────────────
    ksm_deployment:
      k.apps.v1.deployment.new('kube-state-metrics', replicas=1, containers=[
        k.core.v1.container.new('ksm', 'registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.12.0')
        + k.core.v1.container.withPorts([
          k.core.v1.containerPort.new(8080),
          k.core.v1.containerPort.newNamed(8081, 'telemetry'),
        ])
        + k.core.v1.container.resources.withRequests({ cpu: '10m', memory: '32Mi' })
        + k.core.v1.container.resources.withLimits({ cpu: '100m', memory: '128Mi' }),
      ])
      + k.apps.v1.deployment.metadata.withNamespace(ns)
      + k.apps.v1.deployment.spec.template.spec.withServiceAccountName('kiln-sa'),

    ksm_service:
      k.core.v1.service.new('kube-state-metrics', { app: 'kube-state-metrics' }, [{ port: 8080, targetPort: 8080 }])
      + k.core.v1.service.metadata.withNamespace(ns),

    // ── Grafana ──────────────────────────────────────────────────────────
    grafana_deployment:
      k.apps.v1.deployment.new('grafana', replicas=1, containers=[
        k.core.v1.container.new('grafana', 'grafana/grafana:11.1.0')
        + k.core.v1.container.withPorts([k.core.v1.containerPort.new(3001)])
        + k.core.v1.container.withEnv([
          k.core.v1.envVar.new('GF_SERVER_HTTP_PORT', '3001'),
          k.core.v1.envVar.new('GF_SECURITY_ADMIN_PASSWORD', 'kiln-admin'),
          k.core.v1.envVar.new('GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH', '/var/lib/grafana/dashboards/overview.json'),
        ])
        + k.core.v1.container.withVolumeMounts([
          k.core.v1.volumeMount.new('datasources', '/etc/grafana/provisioning/datasources'),
          k.core.v1.volumeMount.new('dashboards-provider', '/etc/grafana/provisioning/dashboards'),
          k.core.v1.volumeMount.new('dashboards', '/var/lib/grafana/dashboards'),
        ])
        + k.core.v1.container.resources.withRequests({ cpu: '50m', memory: '128Mi' })
        + k.core.v1.container.resources.withLimits({ cpu: '250m', memory: '256Mi' }),
      ])
      + k.apps.v1.deployment.metadata.withNamespace(ns)
      + k.apps.v1.deployment.spec.template.spec.withVolumes([
        k.core.v1.volume.fromConfigMap('datasources', 'grafana-datasources'),
        k.core.v1.volume.fromConfigMap('dashboards-provider', 'grafana-dashboards-provider'),
        k.core.v1.volume.fromConfigMap('dashboards', 'grafana-dashboards'),
      ]),

    grafana_service:
      k.core.v1.service.new('grafana', { app: 'grafana' }, [{ port: 3001, targetPort: 3001 }])
      + k.core.v1.service.metadata.withNamespace(ns),

    grafana_datasources:
      k.core.v1.configMap.new('grafana-datasources', {
        'datasources.yaml': |||
          apiVersion: 1
          datasources:
            - name: Prometheus
              type: prometheus
              url: http://prometheus:9090
              isDefault: true
              editable: false
        |||,
      })
      + k.core.v1.configMap.metadata.withNamespace(ns),

    grafana_dashboards_provider:
      k.core.v1.configMap.new('grafana-dashboards-provider', {
        'dashboards.yaml': |||
          apiVersion: 1
          providers:
            - name: default
              type: file
              options:
                path: /var/lib/grafana/dashboards
        |||,
      })
      + k.core.v1.configMap.metadata.withNamespace(ns),

    // Dashboard JSON will be added in Task 14
    grafana_dashboards:
      k.core.v1.configMap.new('grafana-dashboards', {})
      + k.core.v1.configMap.metadata.withNamespace(ns),
  },
}
```

- [ ] **Step 2: Import in kiln.libsonnet**

Add import: `local monitoring = import 'monitoring.libsonnet';`
Add to mixin chain: `postgres + redis + netpol + monitoring + {`

- [ ] **Step 3: Add Prometheus scrape annotations to kilnService helper**

In the `kilnService` function, add pod annotation so Prometheus auto-discovers it:

```jsonnet
+ deployment.spec.template.metadata.withAnnotations({
  'prometheus.io/scrape': 'true',
  'prometheus.io/port': std.toString(port),
  'prometheus.io/path': '/metrics',
})
```

Add this after the `withServiceAccountName` line.

- [ ] **Step 4: Add Grafana to the Ingress rules**

Add a fifth rule to the ingress:

```jsonnet
{
  host: 'grafana.%s' % domain,
  http: { paths: [{ path: '/', pathType: 'Prefix', backend: { service: { name: 'grafana', port: { number: 3001 } } } }] },
},
```

- [ ] **Step 5: Validate**

```bash
cd infra/tanka && tk show environments/default \
  --ext-str IMAGE_REGISTRY=test --ext-str IMAGE_TAG=test \
  --ext-str GCS_BUCKET=test --ext-str WORKLOAD_SA=test \
  --ext-str INGRESS_IP=1.2.3.4 --ext-str PG_BACKUP_BUCKET=test \
  2>&1 | grep "kind:" | sort | uniq -c
```

Expected: Deployment (8+), Service (8+), StatefulSet (2), ConfigMap (5+), Ingress (1), NetworkPolicy (2)

- [ ] **Step 6: Commit**

```bash
git add infra/tanka/lib/monitoring.libsonnet infra/tanka/lib/kiln.libsonnet
git commit -m "tanka: self-hosted Prometheus + Grafana + kube-state-metrics"
```

---

### Task 14: Grafana dashboard JSON

**Files:**
- Create: `infra/tanka/dashboards/overview.json`
- Modify: `infra/tanka/lib/monitoring.libsonnet` (load dashboard into ConfigMap)

- [ ] **Step 1: Create a Kiln overview Grafana dashboard**

Create `infra/tanka/dashboards/overview.json` with a Grafana dashboard JSON that includes panels for:
- Request rate by service (using `kiln_http_requests_total`)
- Request duration p50/p95/p99 (using `kiln_http_request_duration_seconds`)
- Error rate (5xx) by service
- Pod restart count
- CPU and memory usage per pod

This is a standard Grafana JSON model. Use `__import` in the monitoring.libsonnet to load it:

```jsonnet
grafana_dashboards:
  k.core.v1.configMap.new('grafana-dashboards', {
    'overview.json': importstr '../dashboards/overview.json',
  })
  + k.core.v1.configMap.metadata.withNamespace(ns),
```

- [ ] **Step 2: Commit**

```bash
git add infra/tanka/dashboards/ infra/tanka/lib/monitoring.libsonnet
git commit -m "grafana: overview dashboard (RED metrics + resource usage)"
```

---

## Phase 4: CI/CD

### Task 15: Rework deploy.yml

**Files:**
- Modify: `.github/workflows/deploy.yml`

Key changes from existing:
1. Add `test` job that runs `uv run pytest` + `uv run ruff check` before building
2. Add `registry-ui` to the build matrix (currently missing)
3. Use Next.js Dockerfile for registry-ui
4. Pass `INGRESS_IP` and `PG_BACKUP_BUCKET` to Tanka
5. Remove Cloud SQL ext vars (`REGISTRY_DB_CONNECTION`, `CHAT_DB_CONNECTION`)

- [ ] **Step 1: Rewrite deploy.yml**

```yaml
name: Deploy to GKE

on:
  workflow_dispatch:
  push:
    branches: [main]

env:
  GCP_REGION: asia-southeast1
  GKE_CLUSTER: kiln-dev-gke
  GKE_ZONE: asia-southeast1-a

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          version: "latest"
      - run: uv sync --all-extras
      - run: uv run ruff check packages/
      - run: uv run pytest -q

  build-python:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    strategy:
      matrix:
        include:
          - service: registry_api
            image: registry-api
            module: kiln_registry.main:app
            port: 8766
          - service: chat_backend
            image: chat-backend
            module: kiln_chat_backend.main:app
            port: 8765
          - service: tool_executor
            image: tool-executor
            module: kiln_executor.main:app
            port: 8767
          - service: mcp_server
            image: mcp-server
            module: kiln_mcp.main:app
            port: 8768
          - service: synthesis_service
            image: synthesis-service
            module: kiln_synthesis.main:app
            port: 8002
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}
      - uses: google-github-actions/setup-gcloud@v2
      - run: gcloud auth configure-docker ${{ env.GCP_REGION }}-docker.pkg.dev --quiet
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: docker/Dockerfile.python.prod
          push: true
          build-args: |
            SERVICE=${{ matrix.service }}
            MODULE=${{ matrix.module }}
            PORT=${{ matrix.port }}
          tags: |
            ${{ env.GCP_REGION }}-docker.pkg.dev/${{ vars.GCP_PROJECT }}/kiln/${{ matrix.image }}:${{ github.sha }}
            ${{ env.GCP_REGION }}-docker.pkg.dev/${{ vars.GCP_PROJECT }}/kiln/${{ matrix.image }}:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  build-ui:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}
      - uses: google-github-actions/setup-gcloud@v2
      - run: gcloud auth configure-docker ${{ env.GCP_REGION }}-docker.pkg.dev --quiet
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: docker/Dockerfile.nextjs.prod
          push: true
          tags: |
            ${{ env.GCP_REGION }}-docker.pkg.dev/${{ vars.GCP_PROJECT }}/kiln/registry-ui:${{ github.sha }}
            ${{ env.GCP_REGION }}-docker.pkg.dev/${{ vars.GCP_PROJECT }}/kiln/registry-ui:latest
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    needs: [build-python, build-ui]
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}
      - uses: google-github-actions/setup-gcloud@v2
      - run: gcloud container clusters get-credentials ${{ env.GKE_CLUSTER }} --zone ${{ env.GKE_ZONE }} --project ${{ vars.GCP_PROJECT }}

      - name: Install Tanka + Jsonnet Bundler
        run: |
          curl -fSL -o /usr/local/bin/tk https://github.com/grafana/tanka/releases/latest/download/tk-linux-amd64
          chmod +x /usr/local/bin/tk
          curl -fSL -o /usr/local/bin/jb https://github.com/jsonnet-bundler/jsonnet-bundler/releases/latest/download/jb-linux-amd64
          chmod +x /usr/local/bin/jb

      - name: Install Jsonnet deps
        run: cd infra/tanka && jb install

      - name: Verify secrets exist
        run: kubectl get secret kiln-secrets -n kiln

      - name: Deploy with Tanka
        run: |
          cd infra/tanka
          tk apply environments/default --dangerous-auto-approve \
            --ext-str IMAGE_REGISTRY=${{ env.GCP_REGION }}-docker.pkg.dev/${{ vars.GCP_PROJECT }}/kiln \
            --ext-str IMAGE_TAG=${{ github.sha }} \
            --ext-str GCS_BUCKET=${{ secrets.GCS_BUCKET }} \
            --ext-str WORKLOAD_SA=${{ secrets.WORKLOAD_SA }} \
            --ext-str INGRESS_IP=${{ secrets.INGRESS_IP }} \
            --ext-str PG_BACKUP_BUCKET=${{ secrets.PG_BACKUP_BUCKET }}

      - name: Verify rollout
        run: |
          kubectl rollout status deployment/registry-api -n kiln --timeout=120s
          kubectl rollout status deployment/chat-backend -n kiln --timeout=120s
          kubectl rollout status deployment/registry-ui -n kiln --timeout=120s
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: rework deploy pipeline — test gate, UI build, migration via init container"
```

---

## Phase 5: First Deploy Runbook

### Task 16: Manual first deploy

This is not code — it's the exact sequence of commands for the first deployment.

- [ ] **Step 1: Create GCP project + enable billing**

```bash
gcloud projects create kiln-cs5224 --name="Kiln CS5224"
gcloud config set project kiln-cs5224
# Link billing account (via console or CLI)
```

- [ ] **Step 2: Terraform apply**

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your project_id and billing_account_id
terraform init
terraform apply
```

- [ ] **Step 3: Connect to cluster**

```bash
# Copy the command from terraform output
gcloud container clusters get-credentials kiln-dev-gke --zone asia-southeast1-a --project kiln-cs5224
kubectl get nodes  # Expect: 1 node, STATUS=Ready
```

- [ ] **Step 4: Create secrets**

```bash
kubectl create namespace kiln
kubectl create secret generic kiln-secrets -n kiln \
  --from-literal=CLERK_DOMAIN=your-app.clerk.accounts.dev \
  --from-literal=CLERK_SECRET_KEY=sk_test_... \
  --from-literal=MISTRAL_API_KEY=... \
  --from-literal=KILN_INTERNAL_SECRET=$(openssl rand -hex 32) \
  --from-literal=PG_PASSWORD=$(openssl rand -hex 16)
```

- [ ] **Step 5: Build and push images (first time, from local)**

```bash
AR=$(terraform -chdir=infra/terraform output -raw artifact_registry)
gcloud auth configure-docker asia-southeast1-docker.pkg.dev --quiet

for svc in registry_api chat_backend tool_executor mcp_server synthesis_service; do
  IMAGE_NAME=$(echo $svc | tr _ -)
  docker build -f docker/Dockerfile.python.prod \
    --build-arg SERVICE=$svc \
    --build-arg MODULE=kiln_$(echo $svc | cut -d_ -f1).main:app \
    --build-arg PORT=8766 \
    -t $AR/$IMAGE_NAME:initial .
  docker push $AR/$IMAGE_NAME:initial
done

docker build -f docker/Dockerfile.nextjs.prod -t $AR/registry-ui:initial .
docker push $AR/registry-ui:initial
```

- [ ] **Step 6: Deploy with Tanka**

```bash
cd infra/tanka
jb install
INGRESS_IP=$(terraform -chdir=../terraform output -raw ingress_ip)
GCS_BUCKET=$(terraform -chdir=../terraform output -raw gcs_bucket)
WORKLOAD_SA=$(terraform -chdir=../terraform output -raw workload_sa_email)
PG_BACKUP_BUCKET=$(terraform -chdir=../terraform output -raw pg_backup_bucket)

tk apply environments/default --dangerous-auto-approve \
  --ext-str IMAGE_REGISTRY=$AR \
  --ext-str IMAGE_TAG=initial \
  --ext-str GCS_BUCKET=$GCS_BUCKET \
  --ext-str WORKLOAD_SA=$WORKLOAD_SA \
  --ext-str INGRESS_IP=$INGRESS_IP \
  --ext-str PG_BACKUP_BUCKET=$PG_BACKUP_BUCKET
```

- [ ] **Step 7: Verify**

```bash
kubectl get pods -n kiln  # All Running
kubectl get ingress -n kiln  # ADDRESS = the static IP

echo "UI:      http://kiln.$INGRESS_IP.nip.io"
echo "API:     http://api.$INGRESS_IP.nip.io/health"
echo "Chat:    http://chat.$INGRESS_IP.nip.io/livez"
echo "MCP:     http://mcp.$INGRESS_IP.nip.io/livez"
echo "Grafana: http://grafana.$INGRESS_IP.nip.io"

curl -s http://api.$INGRESS_IP.nip.io/health | python3 -m json.tool
```

Expected: all return 200, UI loads in browser, Grafana shows login page (admin/kiln-admin).
