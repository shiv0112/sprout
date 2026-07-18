# Sprout Infrastructure Report

**Team:** Shivansh, Arya Bhosale, Himanshu, Sahajpreet, Chun Jie
**Date:** April 2026

---

## 1. Executive Summary

Sprout is a self-evolving tool registry for autonomous AI agents. Users describe tasks in natural language, a planning agent (ARIA) decomposes them into a directed acyclic task graph, and if a tool is missing, a synthesis agent (Vibe) generates it on the fly. No service restarts are required.

The production deployment runs on **Google Kubernetes Engine (GKE) Standard** in the `asia-southeast1-a` zone under project `peaceful-basis-329822`. The cluster hosts 11 pods (6 application services, 2 data stores, 3 observability components) managed as 33 Kubernetes resources defined in Tanka/Jsonnet manifests.

**Why GKE Standard over Autopilot:** GKE Standard was selected for explicit control over node pools, machine types, and scheduling taints. The burst pool uses preemptible (spot) e2-small nodes with a `NO_SCHEDULE` taint, which is not configurable in Autopilot. Standard also avoids Autopilot's per-pod pricing surcharge, which would have exceeded the budget given 11 always-on pods.

**Cost target:** ~$70/month steady-state against $380 in GCP credits, yielding approximately 5.4 months of runway.

---

## 2. Architecture Overview

```
                        Internet
                            |
                     [Global Static IP]
                    34.36.172.184 (nip.io)
                            |
                   +--------+--------+
                   |  GCE L7 Ingress |
                   |   (HTTP LB)     |
                   +--------+--------+
                            |
          +-----------------+-----------------+------------------+------------------+
          |                 |                 |                  |                  |
  sprout.34.36.172.184  api.34.36.172.184  chat.34.36.172.184  mcp.34.36.172.184  grafana.34.36.172.184
     .nip.io              .nip.io            .nip.io            .nip.io            .nip.io
          |                 |                 |                  |                  |
   +-----------+    +-----------+    +-----------+    +-----------+    +-----------+
   |registry-ui|    |registry-  |    |chat-      |    |mcp-server |    | grafana   |
   |  :3000    |    |api :8766  |    |backend    |    |  :8768    |    |  :3001    |
   +-----------+    +-----------+    |  :8765    |    +-----------+    +-----------+
                          |          +-----------+          |               |
                          |               |                 |               |
                     +----+----+     +----+----+       +----+----+    +-----------+
                     |postgres |     |  redis  |       |registry-|    |prometheus |
                     | :5432   |     |  :6379  |       |api :8766|    |  :9090    |
                     +---------+     +---------+       +---------+    +-----------+
                          |                                                |
                     [10Gi PVC]                                       [5Gi PVC]
                    premium-rwo                                      premium-rwo

              +------------------+    +------------------+
              |synthesis-service |    |  tool-executor   |
              |     :8002        |    |     :8767        |
              +------------------+    +------------------+
              | NetworkPolicy:       | NetworkPolicy:
              |  DNS + registry-api  |  DNS + registry-api
              |  + external :443     |  + external :80,:443
              +------------------+    +------------------+

                     +-------------------+
                     | kube-state-metrics|
                     |  :8080 / :8081    |
                     +-------------------+
```

All application pods run in the `sprout` namespace with a shared Kubernetes ServiceAccount (`sprout-sa`) bound to a GCP service account via Workload Identity Federation.

---

## 3. GKE Cluster Configuration

| Property | Value |
|---|---|
| Cluster name | `sprout-dev-gke` |
| Location | `asia-southeast1-a` (zonal) |
| Release channel | `REGULAR` |
| Kubernetes API version | 1.32 (from jsonnet-libs) |
| VPC | `sprout-dev-vpc` |
| Subnet | `sprout-dev-subnet` (10.0.0.0/20) |
| Pod CIDR | 10.4.0.0/14 (secondary range "pods") |
| Service CIDR | 10.8.0.0/20 (secondary range "services") |
| Private nodes | Yes (master endpoint public, nodes private) |
| Master CIDR | 172.16.0.0/28 |
| Master authorized networks | 0.0.0.0/0 (all) |
| Workload Identity pool | `peaceful-basis-329822.svc.id.goog` |
| Deletion protection | Disabled (dev environment) |

### Node Pools

| Pool | Machine Type | Disk | Count | Spot | Taints |
|---|---|---|---|---|---|
| `main-pool` | e2-standard-2 (2 vCPU, 8 GB) | 50 GB pd-balanced | 1 (fixed) | No | None |
| `burst-pool` | e2-small (0.5 vCPU shared, 2 GB) | 30 GB pd-standard | 0-3 (autoscaling) | Yes | `pool=burst:NoSchedule` |

Both pools have auto-repair and auto-upgrade enabled and use `GKE_METADATA` mode for Workload Identity.

The main pool provides ~1930m allocatable CPU (after system reservation on e2-standard-2). Total CPU requests across all pods sum to approximately 810m (see service inventory below), leaving headroom for kube-system workloads and burst traffic. The burst pool scales to zero when idle and is only scheduled when pods explicitly tolerate the `pool=burst` taint.

---

## 4. Service Inventory

All application services use a shared Tanka helper (`sproutService`) that applies consistent patterns: envFrom for ConfigMap + Secret, Workload Identity ServiceAccount, liveness probe on `/livez`, readiness probe on `/readyz`, and Prometheus scrape annotations.

### Application Pods

| Service | Image | Port | CPU Req / Limit | Mem Req / Limit | Replicas | Probe Paths | Strategy | Notes |
|---|---|---|---|---|---|---|---|---|
| registry-api | `registry-api` | 8766 | 100m / 1000m | 256Mi / 512Mi | 1 | `/livez`, `/readyz` | RollingUpdate | Init container runs Alembic migrations |
| chat-backend | `chat-backend` | 8765 | 100m / 1000m | 256Mi / 512Mi | 1 | `/livez`, `/readyz` | **Recreate** | Redis leader lock enforces single-replica; 30s termination grace |
| mcp-server | `mcp-server` | 8768 | 50m / 250m | 128Mi / 256Mi | 1 | `/livez`, `/readyz` | RollingUpdate | Custom command: `python -m sprout_mcp.main streamable-http` |
| tool-executor | `tool-executor` | 8767 | 50m / 1000m | 256Mi / 512Mi | 1 | `/livez`, `/readyz` | RollingUpdate | NetworkPolicy-restricted egress |
| synthesis-service | `synthesis-service` | 8002 | 100m / 2000m | 512Mi / 1Gi | 1 | `/livez`, `/readyz` | RollingUpdate | NetworkPolicy-restricted egress; highest memory limit for Vibe CLI |
| registry-ui | `registry-ui` | 3000 | 50m / 500m | 128Mi / 256Mi | 1 | `/livez`, `/readyz` | RollingUpdate | Next.js standalone; no Prometheus scrape |

### Data Tier Pods

| Service | Image | Port | CPU Req / Limit | Mem Req / Limit | Replicas | Storage | Probe |
|---|---|---|---|---|---|---|---|
| postgres | `postgres:17-alpine` | 5432 | 100m / 500m | 256Mi / 512Mi | 1 (StatefulSet) | 10Gi PVC (premium-rwo) | `pg_isready -U sprout` |
| redis | `redis:7-alpine` | 6379 | 50m / 200m | 64Mi / 192Mi | 1 (Deployment) | None (ephemeral) | `redis-cli ping` |

### Observability Pods

| Service | Image | Port | CPU Req / Limit | Mem Req / Limit | Replicas | Storage |
|---|---|---|---|---|---|---|
| prometheus | `prom/prometheus:v2.53.0` | 9090 | 100m / 500m | 256Mi / 512Mi | 1 (StatefulSet) | 5Gi PVC (premium-rwo) |
| grafana | `grafana/grafana:11.1.0` | 3001 | 50m / 250m | 128Mi / 256Mi | 1 (Deployment) | None |
| kube-state-metrics | `kube-state-metrics:v2.12.0` | 8080, 8081 | 10m / 100m | 32Mi / 128Mi | 1 (Deployment) | None |

### Aggregate Resource Requests

| Resource | Total Requests | Total Limits |
|---|---|---|
| CPU | 810m | 7300m |
| Memory | 2272Mi | 4608Mi |

---

## 5. Networking

### VPC Design

Terraform provisions a custom-mode VPC (`sprout-dev-vpc`) with a single subnet in `asia-southeast1`:

| Range | CIDR | Purpose |
|---|---|---|
| Primary | 10.0.0.0/20 | Node IPs (4,094 addresses) |
| Secondary "pods" | 10.4.0.0/14 | Pod IPs (262,142 addresses) |
| Secondary "services" | 10.8.0.0/20 | ClusterIP services (4,094 addresses) |

Nodes are private (`enable_private_nodes = true`), meaning they have no external IPs and reach the internet through Cloud NAT (implicit with GKE private nodes). The Kubernetes API endpoint is public (`enable_private_endpoint = false`) with master-authorized-networks set to 0.0.0.0/0.

### Ingress

A GCE L7 HTTP Load Balancer is provisioned via the `kubernetes.io/ingress.class: gce` annotation, backed by a Terraform-managed global static IP (`sprout-dev-ingress-ip`, address `34.36.172.184`).

Host-based routing using nip.io wildcard DNS:

| Host | Backend Service | Port |
|---|---|---|
| `sprout.34.36.172.184.nip.io` | registry-ui | 3000 |
| `api.34.36.172.184.nip.io` | registry-api | 8766 |
| `chat.34.36.172.184.nip.io` | chat-backend | 8765 |
| `mcp.34.36.172.184.nip.io` | mcp-server | 8768 |
| `grafana.34.36.172.184.nip.io` | grafana | 3001 |

CORS is configured in the ConfigMap to allow `https://sprout.<IP>.nip.io`.

### NetworkPolicies

Two egress-only NetworkPolicies lock down the most privileged services:

**synthesis-service (`synthesis-egress`):**
- DNS (UDP/TCP 53) — required for any outbound resolution
- registry-api on port 8766 — for synthesis callback delivery
- External HTTPS (port 443) — for Mistral Vibe API calls
- All RFC 1918 destinations (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) are excluded from the external rule

**tool-executor (`executor-egress`):**
- DNS (UDP/TCP 53)
- registry-api on port 8766 — for tool result reporting
- External HTTP/HTTPS (ports 80, 443) — tools may fetch external data
- Same RFC 1918 exclusion to prevent lateral movement to internal services

Other pods (registry-api, chat-backend, mcp-server, registry-ui) have no NetworkPolicy restrictions and can communicate freely within the cluster.

---

## 6. Data Tier

### PostgreSQL

| Property | Value |
|---|---|
| Image | `postgres:17-alpine` |
| Kind | StatefulSet (1 replica) |
| Database | `sprout_registry` |
| User | `sprout` |
| Password | From `sprout-secrets` Secret (`PG_PASSWORD` key) |
| Storage | 10Gi PVC, `premium-rwo` StorageClass |
| PGDATA | `/var/lib/postgresql/data/pgdata` |
| Service | Headless (`clusterIP: None`) at `postgres:5432` |
| Connection string | `postgresql://sprout:$(PG_PASSWORD)@postgres:5432/sprout_registry` |

**Migrations:** The registry-api Deployment includes an init container that runs `sprout-registry migrate` before the main container starts. This executes Alembic migrations against the database. The migration script location is `sprout_registry/migrations` with versions tracked in `packages/registry_api/sprout_registry/migrations/versions/`. At time of writing, there is one migration: `0001_initial.py`.

**Backups:** A GCS bucket (`peaceful-basis-329822-sprout-pg-backups`) is provisioned with a 14-day lifecycle rule for automatic deletion. The workload service account has `storage.objectCreator` permission on this bucket.

### Redis

| Property | Value |
|---|---|
| Image | `redis:7-alpine` |
| Kind | Deployment (1 replica, no PVC) |
| Service | `redis:6379` |
| Max memory | 128 MB |
| Eviction policy | allkeys-lru |
| Persistence | Disabled (`--save ""`) |

Redis is used as an ephemeral cache and coordination layer, not as durable storage. Its primary roles:

1. **Leader lock for chat-backend:** A Redis-based distributed lock (`sprout:chat_backend:leader` key with 30s TTL) ensures only one chat-backend replica runs at a time. The lock is acquired via `SET NX` on startup. A background heartbeat refreshes the TTL every 10 seconds using a compare-and-set Lua script. If the lock is lost (expiration, eviction, or another replica), the process terminates immediately with `os._exit(1)` to trigger Kubernetes CrashLoopBackOff. This pattern is intentional: chat-backend holds in-memory SSE queues and DAG execution state that cannot be safely split across replicas.

2. **Rate limiting / caching** for inter-service coordination.

The lock is only enforced when `SPROUT_ENV != dev` (production), controlled by the `SPROUT_ENV: prod` value in the ConfigMap.

---

## 7. Observability

### Prometheus

Prometheus v2.53.0 runs as a StatefulSet with 5Gi persistent storage (`premium-rwo`). It scrapes the `sprout` namespace using Kubernetes pod service discovery, selecting pods with the annotation `prometheus.io/scrape: "true"`.

Configuration:
- Scrape interval: 15 seconds
- TSDB retention: 7 days or 2 GB (whichever limit is hit first)
- Web lifecycle API enabled (`--web.enable-lifecycle` for config reload)
- Runs as non-root user 65534 (nobody), fsGroup 65534

Scrape jobs:
1. **kubernetes-pods** — auto-discovers pods in the `sprout` namespace with `prometheus.io/scrape: "true"` annotation. Uses the `prometheus.io/port` and `prometheus.io/path` annotations for target address. The `service` label is populated from the pod's `app` label.
2. **kube-state-metrics** — static target at `kube-state-metrics:8080` for cluster-level Kubernetes object metrics.

### Custom Application Metrics

All Python services (except registry-ui) mount Prometheus metrics via `sprout_shared.metrics.mount_metrics()` at the `/metrics` endpoint:

| Metric | Type | Labels | Description |
|---|---|---|---|
| `sprout_http_requests_total` | Counter | `service`, `method`, `path`, `status` | Total HTTP requests per service |
| `sprout_http_request_duration_seconds` | Histogram | `service`, `method`, `path` | Request latency distribution |

Histogram buckets: 10ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s, 10s.

The `path` label is normalized to the first path segment (e.g., `/tools/123/versions` becomes `/tools`) to prevent high-cardinality label explosion. The `/metrics` endpoint itself is excluded from instrumentation to avoid self-referential loops.

Services with Prometheus scrape enabled: registry-api, chat-backend, tool-executor, mcp-server, synthesis-service (5 services). Registry-ui has `prometheus_scrape: false`.

### Grafana

Grafana 11.1.0 is provisioned with:
- Auto-configured Prometheus datasource pointing to `http://prometheus:9090`
- Dashboard provisioning directory at `/var/lib/grafana/dashboards` (empty dir, dashboards created via UI)
- Admin password sourced from `sprout-secrets` Secret (`GRAFANA_PASSWORD` key)
- Custom HTTP port 3001 (to avoid conflict with registry-ui on 3000)
- Accessible at `grafana.34.36.172.184.nip.io`

### Kube-State-Metrics

kube-state-metrics v2.12.0 exposes Kubernetes object metrics (pod status, deployment replicas, resource requests/limits, etc.) on port 8080, scraped by Prometheus. Telemetry self-metrics on port 8081.

---

## 8. Security

### Workload Identity Federation (GKE Workloads)

All pods in the `sprout` namespace use the `sprout-sa` Kubernetes ServiceAccount, which is annotated with:
```
iam.gke.io/gcp-service-account: sprout-dev-workload@peaceful-basis-329822.iam.gserviceaccount.com
```

The GCP service account (`sprout-dev-workload`) has the following IAM bindings:

| Role | Resource | Purpose |
|---|---|---|
| `roles/iam.workloadIdentityUser` | SA binding for `sprout/sprout-sa` | Allows pods to impersonate the GCP SA |
| `roles/storage.objectAdmin` | `peaceful-basis-329822-sprout-tools` bucket | Read/write tool artifacts |
| `roles/storage.objectCreator` | `peaceful-basis-329822-sprout-pg-backups` bucket | Write database backups |
| `roles/artifactregistry.reader` | Project-level | Pull container images from Artifact Registry |

### Workload Identity Federation (GitHub Actions)

CI/CD authenticates via OIDC, eliminating long-lived service account keys:

| Component | Value |
|---|---|
| WIF Pool | `github-actions` |
| WIF Provider | `github-oidc` |
| OIDC Issuer | `https://token.actions.githubusercontent.com` |
| Attribute condition | `assertion.repository == 'aryabyte21/sprout'` |
| GCP Service Account | `sprout-dev-github-ci` |
| Roles | `roles/artifactregistry.writer`, `roles/container.developer` |

The attribute condition restricts token exchange to the `aryabyte21/sprout` repository only. The CI service account can push images and deploy to GKE but has no access to storage buckets or secrets.

### Secret Management

Secrets are stored in a Kubernetes Secret named `sprout-secrets` (created manually, not in Terraform or Tanka manifests). Referenced keys:

| Key | Consumers |
|---|---|
| `PG_PASSWORD` | All Python services (via DATABASE_URL), postgres container |
| `GRAFANA_PASSWORD` | Grafana container (GF_SECURITY_ADMIN_PASSWORD) |
| Additional app secrets | All Python services (via envFrom secretRef) |

### Container Security

- All Python service containers run as non-root user `sprout` (created via `groupadd`/`useradd` in the Dockerfile)
- The Next.js container runs as non-root user `sprout` (UID 1001, created via `adduser`)
- Prometheus runs as UID/GID 65534 (nobody) with `fsGroup: 65534`
- Base images: `python:3.12-slim` (Python services), `node:22-alpine` (UI), `postgres:17-alpine`, `redis:7-alpine`

### Network Isolation

Synthesis-service and tool-executor have egress-only NetworkPolicies that prevent lateral movement to internal services while allowing required external API calls. See Section 5 for details.

---

## 9. CI/CD Pipeline

### Workflow: `CD -- Build, Push, Deploy to GKE`

**Triggers:** Push to `main` branch, or manual `workflow_dispatch`.

```
+----------+     +------------------+     +----------+     +--------+
|   test   |---->| build-python (5x)|---->|  deploy  |---->|verify  |
|          |     | build-ui    (1x) |     |  (Tanka) |     |rollouts|
+----------+     +------------------+     +----------+     +--------+
```

#### Stage 1: Test Gate (`test`)

Runs on `ubuntu-latest`:
1. Checkout code
2. Install uv via `astral-sh/setup-uv@v4`
3. `uv sync --all-extras` (install all Python dependencies)
4. `uv run ruff check packages/` (lint)
5. `uv run pytest -q` (unit tests)

All subsequent stages depend on `test` passing.

#### Stage 2: Build (parallel)

**build-python** — Strategy matrix builds 5 images in parallel:

| Matrix entry | Image name | Module | Port |
|---|---|---|---|
| `registry_api` | `registry-api` | `sprout_registry.main:app` | 8766 |
| `chat_backend` | `chat-backend` | `sprout_chat_backend.main:app` | 8765 |
| `tool_executor` | `tool-executor` | `sprout_executor.main:app` | 8767 |
| `mcp_server` | `mcp-server` | `sprout_mcp.main:app` | 8768 |
| `synthesis_service` | `synthesis-service` | `sprout_synthesis.main:app` | 8002 |

Each image is built from `docker/Dockerfile.python.prod` (multi-stage: `python:3.12-slim` builder + runtime) and tagged with both `$GITHUB_SHA` and `latest`. Docker BuildX with GitHub Actions cache (`type=gha`).

**build-ui** — Single image from `docker/Dockerfile.nextjs.prod` (3-stage: deps, build, runtime with `node:22-alpine`). Tagged identically.

All images are pushed to Artifact Registry: `asia-southeast1-docker.pkg.dev/peaceful-basis-329822/sprout/<image>`.

Authentication: WIF OIDC (`google-github-actions/auth@v2`) with the CI service account.

#### Stage 3: Deploy (`deploy`)

1. Authenticate via WIF
2. Get GKE credentials: `gcloud container clusters get-credentials sprout-dev-gke --zone asia-southeast1-a`
3. Install Tanka and Jsonnet Bundler (latest Linux amd64 binaries)
4. `jb install` (fetch k8s-libsonnet 1.32 vendor libs)
5. Verify `sprout-secrets` Secret exists in the cluster
6. `tk apply environments/default` with external variables:
   - `IMAGE_REGISTRY`, `IMAGE_TAG` (from build stage)
   - `GCS_BUCKET`, `WORKLOAD_SA`, `INGRESS_IP`, `INGRESS_IP_NAME`, `PG_BACKUP_BUCKET` (from GitHub Secrets)
7. `--dangerous-auto-approve` skips the interactive diff prompt

#### Stage 4: Rollout Verification

Waits (up to 120s each) for rollout completion of:
- `registry-api`
- `chat-backend`
- `mcp-server`
- `synthesis-service`
- `registry-ui`

Note: `tool-executor` rollout is not explicitly verified in the workflow.

---

## 10. Cost Analysis

### Monthly Breakdown (Estimated)

| Resource | Specification | Monthly Cost (est.) |
|---|---|---|
| GKE cluster management fee | Zonal Standard (free tier) | $0 |
| e2-standard-2 node (main-pool) | 1x always-on, on-demand | ~$49 |
| e2-small spot nodes (burst-pool) | 0-3x, autoscaling, spot pricing | ~$0 (idle) to $7 (sustained) |
| PD Balanced (main node) | 50 GB | ~$5 |
| PD Standard (burst nodes) | 30 GB x 0-3 | ~$0-4 |
| Premium-RWO PVCs | 10 Gi (postgres) + 5 Gi (prometheus) | ~$3 |
| Global static IP | 1x (in use, no idle charge) | $0 |
| GCE L7 Load Balancer | 1x forwarding rule + 5 backends | ~$8 |
| Artifact Registry | ~6 images, multi-layer | ~$1 |
| GCS Buckets | 2x (tools + pg-backups, minimal data) | ~$1 |
| Cloud NAT (implicit) | Egress for private nodes | ~$2-3 |
| **Total steady-state** | | **~$70/month** |

### Budget Configuration

Terraform provisions a billing budget of **$300** with threshold alerts at:
- 16.67% ($50) — early warning
- 50% ($150) — midpoint
- 83.33% ($250) — approaching limit
- 100% ($300) — budget exceeded

### Runway

With $380 in GCP credits and ~$70/month steady-state cost, the project has approximately **5.4 months** of runway. The budget alerts are configured conservatively below the actual credit limit to provide advance warning.

### Comparison to Original Cloud Run Plan

The preliminary report proposed Cloud Run + Cloud SQL + Pub/Sub. GKE Standard was chosen instead for the following reasons:

| Factor | Cloud Run | GKE Standard (actual) |
|---|---|---|
| Persistent connections (SSE) | Timeout after 60 min; no sticky sessions | Full control, no timeout limits |
| In-cluster communication | Via public endpoints or VPC connector ($) | Free ClusterIP service mesh |
| Database | Cloud SQL (~$25/mo for db-f1-micro) | In-cluster Postgres (free, included in node cost) |
| Observability | Cloud Monitoring (limited free tier) | Self-hosted Prometheus + Grafana (free) |
| Complexity | Simpler per-service, complex for inter-service | More initial setup, simpler inter-service |
| Monthly cost | ~$90-120 (Cloud SQL + VPC connector + egress) | ~$70 |

---

## 11. Deployment Runbook

### Prerequisites

- `gcloud` CLI authenticated to `peaceful-basis-329822`
- `terraform` >= 1.5
- `kubectl`
- `tk` (Tanka) and `jb` (Jsonnet Bundler)
- `docker` with BuildX

### Step 1: Provision Infrastructure (one-time)

```bash
cd infra/terraform

# Initialize and apply
terraform init
terraform plan -var="project_id=peaceful-basis-329822"
terraform apply -var="project_id=peaceful-basis-329822"

# Note the outputs
terraform output
```

### Step 2: Connect to Cluster

```bash
gcloud container clusters get-credentials sprout-dev-gke \
  --zone asia-southeast1-a \
  --project peaceful-basis-329822
```

### Step 3: Create Secrets

```bash
kubectl create namespace sprout

kubectl create secret generic sprout-secrets \
  --namespace sprout \
  --from-literal=PG_PASSWORD='<postgres-password>' \
  --from-literal=GRAFANA_PASSWORD='<grafana-admin-password>' \
  --from-literal=MISTRAL_API_KEY='<mistral-key>' \
  --from-literal=CLERK_SECRET_KEY='<clerk-secret>' \
  --from-literal=SPROUT_API_KEY='<internal-api-key>'
```

### Step 4: Build and Push Images

```bash
GAR=asia-southeast1-docker.pkg.dev/peaceful-basis-329822/sprout
TAG=$(git rev-parse HEAD)

gcloud auth configure-docker asia-southeast1-docker.pkg.dev --quiet

# Build all Python services
for svc in registry_api:sprout_registry.main:app:8766:registry-api \
           chat_backend:sprout_chat_backend.main:app:8765:chat-backend \
           tool_executor:sprout_executor.main:app:8767:tool-executor \
           mcp_server:sprout_mcp.main:app:8768:mcp-server \
           synthesis_service:sprout_synthesis.main:app:8002:synthesis-service; do
  IFS=: read -r SERVICE MODULE PORT IMAGE <<< "$svc"
  docker build -f docker/Dockerfile.python.prod \
    --build-arg SERVICE="$SERVICE" \
    --build-arg MODULE="$MODULE" \
    --build-arg PORT="$PORT" \
    -t "$GAR/$IMAGE:$TAG" -t "$GAR/$IMAGE:latest" .
  docker push "$GAR/$IMAGE:$TAG"
  docker push "$GAR/$IMAGE:latest"
done

# Build UI
docker build -f docker/Dockerfile.nextjs.prod \
  -t "$GAR/registry-ui:$TAG" -t "$GAR/registry-ui:latest" .
docker push "$GAR/registry-ui:$TAG"
docker push "$GAR/registry-ui:latest"
```

### Step 5: Deploy with Tanka

```bash
cd infra/tanka
jb install

tk apply environments/default \
  --ext-str IMAGE_REGISTRY="asia-southeast1-docker.pkg.dev/peaceful-basis-329822/sprout" \
  --ext-str IMAGE_TAG="$TAG" \
  --ext-str GCS_BUCKET="peaceful-basis-329822-sprout-tools" \
  --ext-str WORKLOAD_SA="sprout-dev-workload@peaceful-basis-329822.iam.gserviceaccount.com" \
  --ext-str INGRESS_IP="34.36.172.184" \
  --ext-str INGRESS_IP_NAME="sprout-dev-ingress-ip" \
  --ext-str PG_BACKUP_BUCKET="peaceful-basis-329822-sprout-pg-backups"
```

### Step 6: Verify

```bash
kubectl get pods -n sprout
kubectl rollout status deployment/registry-api -n sprout --timeout=120s
kubectl rollout status deployment/chat-backend -n sprout --timeout=120s
kubectl rollout status deployment/mcp-server -n sprout --timeout=120s
kubectl rollout status deployment/synthesis-service -n sprout --timeout=120s
kubectl rollout status deployment/registry-ui -n sprout --timeout=120s

# Check endpoints
curl -s http://sprout.34.36.172.184.nip.io/livez
curl -s http://api.34.36.172.184.nip.io/livez
```

---

## 12. Divergences from Preliminary Report

| Aspect | Preliminary Report | Actual Deployment | Rationale |
|---|---|---|---|
| Compute | Cloud Run (serverless) | GKE Standard (zonal, 1+0-3 nodes) | SSE streaming, inter-service communication, cost control |
| Database | Cloud SQL for PostgreSQL | In-cluster PostgreSQL 17 (StatefulSet, 10Gi PVC) | Eliminates Cloud SQL per-instance cost ($25+/mo); sufficient for a dev/demo workload |
| Auth | Firebase Authentication | Clerk (JWT + API keys) | Better DX, built-in Next.js integration, dual auth pattern (browser + CLI) |
| Messaging | Pub/Sub for event delivery | HTTP callbacks (synthesis -> registry-api) | Simpler for point-to-point delivery; Pub/Sub fan-out not needed |
| Monitoring | Cloud Monitoring + Cloud Logging | Self-hosted Prometheus v2.53.0 + Grafana 11.1.0 + kube-state-metrics v2.12.0 | Zero marginal cost, full metric control, custom dashboards |
| Config management | YAML manifests | Tanka + Jsonnet (k8s-libsonnet 1.32) | Programmable manifests eliminate duplication across 11 services; 33 resources from ~500 lines of Jsonnet |
| CI/CD | Cloud Build | GitHub Actions with WIF OIDC | Existing GitHub repo, no additional GCP service needed, matrix builds |
| Container registry | Container Registry (deprecated) | Artifact Registry (asia-southeast1) | Google's recommended replacement, regional for latency |
| Tool storage | Inline in database | GCS bucket (`peaceful-basis-329822-sprout-tools`) with versioning (5 versions retained) | Separates tool artifacts from metadata; enables future CDN serving |
| DNS | Custom domain with Cloud DNS | nip.io wildcard DNS | No domain registration required; sufficient for demo/academic use |

---

## Appendix A: Terraform Outputs

| Output | Description |
|---|---|
| `gke_cluster_name` | `sprout-dev-gke` |
| `gke_cluster_zone` | `asia-southeast1-a` |
| `artifact_registry_url` | `asia-southeast1-docker.pkg.dev/peaceful-basis-329822/sprout` |
| `gcs_tools_bucket` | `peaceful-basis-329822-sprout-tools` |
| `gcs_pg_backups_bucket` | `peaceful-basis-329822-sprout-pg-backups` |
| `ingress_static_ip` | `34.36.172.184` |
| `nip_io_domain` | `sprout.34.36.172.184.nip.io` |
| `wif_provider` | Full provider resource path for GitHub Actions OIDC |

## Appendix B: GCP APIs Enabled

- `container.googleapis.com` (GKE)
- `artifactregistry.googleapis.com` (Artifact Registry)
- `compute.googleapis.com` (Compute Engine, networking)
- `iam.googleapis.com` (IAM, WIF)
- `billingbudgets.googleapis.com` (Budget alerts)
- `secretmanager.googleapis.com` (Secret Manager)
- `cloudresourcemanager.googleapis.com` (Project metadata)

## Appendix C: Resource Count Breakdown (33 Total)

| Source | Resources | Count |
|---|---|---|
| `sprout.libsonnet` | namespace, serviceAccount, configMap, 6x deployment+service pairs, ingress | 16 |
| `postgres.libsonnet` | StatefulSet (with PVC template), headless Service | 2 |
| `redis.libsonnet` | Deployment, Service | 2 |
| `monitoring.libsonnet` | prometheus ConfigMap, StatefulSet (with PVC), Service; kube-state-metrics Deployment, Service; grafana datasources ConfigMap, dashboards-provider ConfigMap, Deployment, Service | 9 |
| `networkpolicies.libsonnet` | synthesis-egress NetworkPolicy, executor-egress NetworkPolicy | 2 |
| **PVC templates** | postgres pgdata PVC, prometheus data PVC (materialized by StatefulSet controller) | 2 |
| **Total** | | **33** |
