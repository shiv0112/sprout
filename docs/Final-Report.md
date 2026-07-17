# Kiln: A Self-Evolving Tool Registry for Autonomous AI Agents

**CS5224 Cloud Computing --- Final Report**

Shivansh (A0328697H), Arya Bhosale (A0314484A), Himanshu (A0314584B), Sahajpreet (A0313530W), Chun Jie (A0252615J)

National University of Singapore, AY2025/26 Semester 2

---

## 1. Abstract

Kiln is a self-evolving tool registry for autonomous AI agents, deployed on Google Kubernetes Engine (GKE) in GCP's `asia-southeast1` region. The platform enables AI agents to discover, execute, and synthesise tools at runtime via natural language: a planning agent decomposes queries into task DAGs, executes them against a shared registry of 40 tools, and triggers LLM-powered synthesis when a tool is missing. The system comprises six Python microservices and one Next.js frontend, orchestrated across 33 Kubernetes resources on a single `e2-standard-2` node. Infrastructure is fully codified in Terraform (GCP resources) and Tanka/Jsonnet (Kubernetes manifests), with a GitHub Actions pipeline that runs 201 automated tests, builds container images, and deploys to GKE on every push to `main`. Steady-state cost is approximately $70/month on $380 of GCP credits, giving roughly five months of runway.

---

## 2. Introduction and Motivation

### 2.1 Problem Statement

Modern AI agents built on frameworks such as LangChain, CrewAI, AutoGen, and Anthropic's Model Context Protocol (MCP) are fundamentally constrained by static, pre-defined toolsets. When an agent encounters a task requiring a capability that has not been built --- fetching real-time stock prices, geocoding an address, analysing the sentiment of a product review --- the agent simply fails. Extending an agent's capabilities today requires a developer to manually write the tool code, register it in the agent framework, and restart the service. The problem is compounded by fragmentation across ecosystems: a weather tool written for LangChain cannot be used in CrewAI without rewriting it.

### 2.2 Proposed Solution

Kiln is a cloud-native SaaS platform that provides AI agents with a self-healing, expanding tool ecosystem. When an agent encounters a task requiring a tool that does not exist, Kiln autonomously synthesises it via LLM-powered code generation inside a gVisor-sandboxed Cloud Run instance, validates the generated code against a multi-stage quality gate (JSON schema validation, test fixture execution, dependency safety checks), registers the tool in the platform's registry, and makes it available at runtime to every agent that connects to the platform via HTTP or MCP. Once created, the tool becomes permanently available with zero downtime and zero human intervention.

Kiln exposes its entire tool library via the Model Context Protocol (MCP specification version 2025-11-25), the emerging open standard for how AI assistants discover and invoke external tools. Any MCP-compatible client --- Claude Desktop, Cursor, VS Code Copilot, Windsurf --- can connect to Kiln's MCP Server and immediately access every tool in the registry. When new tools are synthesised, the MCP Server emits `notifications/tools/list_changed` notifications over the JSON-RPC protocol, prompting all connected AI clients to automatically refresh their tool catalogues without any manual intervention.

### 2.3 Why Cloud

Three factors make cloud deployment essential for Kiln:

1. **Global accessibility.** MCP clients and browser users must reach the registry from anywhere. A public endpoint with managed TLS and load balancing is a hard requirement.
2. **Managed infrastructure.** Operating PostgreSQL, Redis, container orchestration, and a CI/CD pipeline on bare metal would consume the team's entire bandwidth. GKE, Artifact Registry, and GitHub Actions eliminate that overhead.
3. **Elastic compute for synthesis.** Tool synthesis is CPU-intensive and bursty. A burst node pool with spot instances (0--3 nodes) absorbs spikes without paying for idle capacity.

---

## 3. System Architecture

### 3.1 Overview

Kiln follows a microservices architecture deployed on a single GKE cluster. The frontend communicates with backend services through a GCE Ingress that routes traffic based on hostname. All backend services share a PostgreSQL database and a Redis instance for coordination.

```
                        Internet
                           |
                    [GCE Ingress]
                     (nip.io DNS)
                           |
          +-------+--------+--------+--------+---------+
          |       |        |        |        |         |
       kiln.*  api.*    chat.*   mcp.*   grafana.*
          |       |        |        |        |         
   [registry  [registry [chat   [mcp     [grafana]
     -ui]       -api]   backend] server]             
                  |        |        |
                  +---+----+--------+
                      |
              [synthesis-service]
                      |
            [tool-executor] (stub)
                      |
         +------------+------------+
         |            |            |
    [PostgreSQL]   [Redis]      [GCS]
    (StatefulSet)  (Deployment)  (Bucket)
```

**Data flow for a chat request:**

1. User sends a natural language query via the React frontend to `chat-backend`.
2. The planning agent (Mistral Large) decomposes the query into a directed acyclic graph (DAG) of subtasks.
3. `KilnGraphFlow` executes the DAG in topological order via Kahn's algorithm, calling `registry-api` for each tool invocation.
4. `registry-api` loads the tool spec from its in-memory cache, executes the Python implementation, and returns the result.
5. Progress events stream back to the frontend via Server-Sent Events (SSE).
6. If a required tool is missing, `chat-backend` triggers `synthesis-service`, which generates a `spec.yaml` + `impl.py` via Mistral Vibe CLI and registers the new tool via an HTTP callback to `registry-api`.

### 3.2 Backend Services

The platform consists of six Python microservices, each deployed as a separate Kubernetes Deployment with independent resource limits and health probes.

| Service | Port | Responsibilities | CPU Req / Limit | Memory Req / Limit |
|---------|------|-----------------|----------------|-------------------|
| **registry-api** | 8766 | Tool CRUD, spec loading, full-text search, execution dispatch, synthesis callbacks, Alembic migrations (init container) | 100m / 1000m | 256Mi / 512Mi |
| **chat-backend** | 8765 | LLM planning (Mistral Large), DAG execution (Kahn's algorithm), SSE streaming, user API key vault | 100m / 1000m | 256Mi / 512Mi |
| **mcp-server** | 8768 | MCP JSON-RPC bridge, `tools/list`, `tools/call`, `notifications/tools/list_changed`, streamable-http transport | 50m / 250m | 128Mi / 256Mi |
| **synthesis-service** | 8002 | LLM code generation (Mistral Vibe CLI), `spec.yaml` + `impl.py` production, HTTP callback to registry | 100m / 2000m | 512Mi / 1Gi |
| **tool-executor** | 8767 | Stub for future gVisor-sandboxed execution; currently proxies to registry-api | 50m / 1000m | 256Mi / 512Mi |
| **registry-ui** | 3000 | Next.js 16 static/SSR frontend (production standalone build) | 50m / 500m | 128Mi / 256Mi |

Total CPU requests across all six services: **450m** on a node with 1930m allocatable capacity (23% utilisation). This leaves substantial headroom for traffic spikes before the burst pool needs to engage.

### 3.3 Frontend

The frontend is a Next.js 16 application using the App Router, React 19, TypeScript 5.9, and Tailwind CSS 4. Key features:

- **Clerk authentication** with JWT-based session management, supporting both browser and API key flows.
- **Vercel AI SDK** for chat streaming via `useChat`, connected to the `chat-backend` SSE endpoint.
- **TanStack Query** for data fetching with automatic cache invalidation.
- **Semantic + keyword search** unified in a single search bar, with BM25 ranking and optional Mistral embeddings.
- **DAG visualisation** that renders the task execution graph in real time as nodes complete.

### 3.4 Data Layer

| Store | Technology | Purpose | Configuration |
|-------|-----------|---------|---------------|
| **PostgreSQL** | PostgreSQL 17 Alpine (StatefulSet) | Tool metadata, execution statistics, user data | 10Gi `premium-rwo` PVC, headless ClusterIP service |
| **Redis** | Redis 7 Alpine (Deployment) | Leader lock for chat-backend, ephemeral cache | 128MB max memory, `allkeys-lru` eviction, persistence disabled |
| **GCS** | Google Cloud Storage bucket | Per-user tool artifacts (`spec.yaml`, `impl.py`, `requirements.txt`) | Versioning enabled, lifecycle rule retains 5 versions |
| **GCS (backups)** | Separate GCS bucket | PostgreSQL backup dumps | 14-day lifecycle deletion |

The database layer (`kiln_registry/db.py`) uses SQLAlchemy 2.0 with `asyncpg` for PostgreSQL and falls back to `aiosqlite` for local development, making the codebase zero-config for developers while fully async in production. Schema changes are managed via Alembic migrations, applied by a Kubernetes init container before the `registry-api` pod starts --- replacing the preliminary report's `create_all()` approach, which risked data loss on schema changes.

### 3.5 MCP Integration

The MCP Server (`kiln_mcp`, port 8768) bridges the Model Context Protocol (version 2025-11-25) to Kiln's REST API. It exposes every registered tool as an MCP tool via `tools/list` and `tools/call`, supports the streamable-http transport for persistent connections, and emits `notifications/tools/list_changed` when the tool catalogue changes (e.g., after a synthesis completes). This allows any MCP-compatible AI assistant to use Kiln tools without installing anything --- they connect to a single endpoint and the full catalogue is immediately available. The server includes an OAuth 2.1 provider backed by Clerk for client authentication.

---

## 4. Cloud Deployment Architecture

### 4.1 Infrastructure as Code

All cloud infrastructure is codified in two layers:

**Terraform** (`infra/terraform/`, 342 lines) provisions GCP-level resources:
- VPC with custom subnet (`10.0.0.0/20`) and secondary ranges for pods (`10.4.0.0/14`) and services (`10.8.0.0/20`)
- GKE Standard zonal cluster with Workload Identity
- Two node pools (main + burst)
- Artifact Registry for container images
- Two GCS buckets (tool artifacts + PostgreSQL backups)
- IAM service accounts with Workload Identity Federation bindings
- Budget alerts at 16.7%, 50%, 83.3%, and 100% of a $300 threshold

**Tanka/Jsonnet** (`infra/tanka/`, 514 lines across 6 libsonnet files) declares all Kubernetes resources:
- 9 Deployments (6 application services + Redis + Grafana + kube-state-metrics)
- 11 Services (one per deployment + headless Postgres service)
- 2 StatefulSets (PostgreSQL + Prometheus)
- 4 ConfigMaps (application config + Prometheus config + Grafana datasources + Grafana dashboards provider)
- 2 NetworkPolicies (synthesis egress + executor egress)
- 1 Ingress (5-host GCE Ingress with static IP)
- 1 Namespace, 1 ServiceAccount, 2 PVCs

Total: **33 Kubernetes resources**, all generated from parameterised Jsonnet templates.

### 4.2 GKE Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Cluster type | GKE Standard (zonal) | Free control plane ($0 vs $73/mo for Autopilot management fee); acceptable for a demo/academic workload |
| Zone | `asia-southeast1-a` | Lowest latency from Singapore |
| Main node pool | 1x `e2-standard-2` (2 vCPU, 8 GB RAM, 50 GB `pd-balanced`) | Fits all 33 resources at 23% CPU utilisation |
| Burst node pool | 0--3x `e2-small` (spot instances, 30 GB `pd-standard`) | Scale-to-zero for synthesis bursts; `NO_SCHEDULE` taint prevents regular workloads from landing on burst nodes |
| Release channel | REGULAR | Automatic minor version upgrades |
| Private nodes | Enabled (NAT through master) | Nodes have no public IPs; egress via Cloud NAT |
| Workload Identity | Enabled | Pods authenticate to GCP APIs via Kubernetes ServiceAccount annotations, eliminating static credentials |

### 4.3 Networking

**VPC.** A custom VPC with a `/20` node subnet and separate secondary ranges for pods and services. Private cluster configuration ensures nodes have no public IP addresses.

**Ingress.** A single GCE Ingress resource with a reserved global static IP routes traffic to five backends based on hostname:

| Hostname Pattern | Backend |
|-----------------|---------|
| `kiln.<IP>.nip.io` | registry-ui (port 3000) |
| `api.<IP>.nip.io` | registry-api (port 8766) |
| `chat.<IP>.nip.io` | chat-backend (port 8765) |
| `mcp.<IP>.nip.io` | mcp-server (port 8768) |
| `grafana.<IP>.nip.io` | grafana (port 3001) |

We use `nip.io` wildcard DNS to avoid purchasing a domain for a course project. This maps any `<name>.<IP>.nip.io` to the IP address, giving us host-based routing without DNS configuration.

**NetworkPolicies.** Two egress-only policies restrict outbound traffic from security-sensitive services:

- `synthesis-egress`: The synthesis service can only reach DNS, registry-api (port 8766), and external HTTPS (port 443, excluding RFC 1918 ranges). This prevents a compromised synthesis container from accessing internal infrastructure.
- `executor-egress`: The tool executor has the same restrictions plus HTTP (port 80) for tools that fetch public APIs.

### 4.4 Observability

The observability stack runs entirely in-cluster to avoid the cost of GCP's managed monitoring services:

**Prometheus** (StatefulSet, 5Gi `premium-rwo` PVC):
- Scrapes all application pods annotated with `prometheus.io/scrape: "true"` every 15 seconds.
- Collects kube-state-metrics for cluster-level signals (pod restarts, resource pressure).
- 7-day retention with a 2GB size cap.

**Grafana** (Deployment, port 3001):
- Pre-configured with Prometheus as the default datasource via provisioning ConfigMaps.
- Accessible externally via the `grafana.<IP>.nip.io` Ingress host.
- Password sourced from the `kiln-secrets` Kubernetes Secret.

**Application metrics** (`kiln_shared/metrics.py`):
- Every Python service exposes a `/metrics` endpoint via `prometheus_client`.
- Two custom metrics:
  - `kiln_http_requests_total` (Counter): total requests labelled by service, method, path, and status code.
  - `kiln_http_request_duration_seconds` (Histogram): request latency with buckets from 10ms to 10s.
- Metrics middleware is mounted via `mount_metrics(app, service_name)` in each service's FastAPI application.

### 4.5 CI/CD Pipeline

The deployment pipeline (`.github/workflows/deploy.yml`) triggers on every push to `main` and consists of four stages:

```
push to main
     |
     v
  [test] ---- uv sync, ruff check, pytest (201 tests)
     |
     +------> [build-python] x5 ---- matrix build for 5 Python services
     |              |
     +------> [build-ui] x1 ---- Next.js standalone build
                    |
                    v
              [deploy] ---- Tanka apply to GKE, rollout verification
```

**Authentication.** The pipeline uses Workload Identity Federation (WIF) to authenticate to GCP. GitHub Actions receives an OIDC token, exchanges it for a GCP access token via the `google-github-actions/auth` action, and authenticates to Artifact Registry and GKE. No static service account keys are stored in GitHub Secrets.

**Build.** A single multi-stage Dockerfile (`docker/Dockerfile.python.prod`) is parameterised with `SERVICE`, `MODULE`, and `PORT` build args, producing five distinct images from one template. The Next.js frontend has its own Dockerfile (`docker/Dockerfile.nextjs.prod`). All images are pushed to Artifact Registry with both `<sha>` and `latest` tags. Builds use GitHub Actions cache (`type=gha`) for Docker layer reuse.

**Deploy.** The deploy job installs Tanka and Jsonnet Bundler, fetches GKE credentials, verifies that the `kiln-secrets` Secret exists, then runs `tk apply` with external variables for image tag, registry URL, and infrastructure references. After applying, it verifies rollout status for all five application deployments with a 120-second timeout.

### 4.6 Security

**Workload Identity Federation.** Both the CI/CD pipeline and in-cluster workloads use WIF to authenticate to GCP. The CI service account has `artifactregistry.writer` and `container.developer` roles. The workload service account has `storage.objectAdmin` on the tools bucket and `storage.objectCreator` on the backup bucket.

**Kubernetes Secrets.** Sensitive values (database password, API keys, Grafana password) are stored in a `kiln-secrets` Kubernetes Secret, created manually before the first deploy. The CI pipeline verifies its existence as a preflight check.

**NetworkPolicies.** As described in Section 4.3, egress-only policies on the synthesis and executor services restrict outbound traffic to known destinations.

**Leader Lock.** The `chat-backend` service maintains in-memory SSE queues and DAG state that cannot be replicated across pods. A Redis-based leader lock (`leader_lock.py`) enforces the single-replica invariant:
- On startup, the pod attempts `SET NX` on a Redis key with a 30-second TTL.
- A background task refreshes the TTL every 10 seconds via a compare-and-set Lua script.
- If another replica already holds the lock, the new pod exits immediately with `sys.exit(1)`, surfacing as a Kubernetes `CrashLoopBackOff`.
- If the heartbeat ever loses the lock (expiry, eviction, or hijack), the process terminates with `os._exit(1)` to avoid split-brain traffic.

**Non-root containers.** The production Dockerfile creates a dedicated `kiln` user and runs the application as that user.

---

## 5. Key Design Decisions (Changes from Preliminary Report)

The preliminary report proposed a Cloud Run-based architecture with Firebase Auth, Cloud SQL, and Cloud Pub/Sub. The final system diverges substantially. The table below summarises every major change and its rationale.

| Decision | Preliminary Plan | Final Implementation | Rationale |
|----------|-----------------|---------------------|-----------|
| **Compute platform** | Cloud Run (5 services, scale-to-zero) | GKE Standard (zonal, 1 node + burst pool) | Cloud Run's per-request billing is ideal at near-zero traffic, but GKE Standard has a free control plane and gives us full control over scheduling, networking, and observability. At our steady-state workload, a single `e2-standard-2` node is cheaper than 5 always-warm Cloud Run services. |
| **Database** | Cloud SQL (2 instances, shared-core, ~$8/mo) | In-cluster PostgreSQL 17 StatefulSet (10Gi PVC) | Cloud SQL's minimum cost is ~$8/mo per instance. An in-cluster Postgres StatefulSet on the existing node costs only the marginal PVC storage (~$0.40/mo for 10Gi `premium-rwo`). Acceptable trade-off for a demo-scale workload with manual backup to GCS. |
| **Authentication** | Firebase Auth (JWT issuer, free tier) | Clerk (JWT issuer, free tier for <10K MAU) | Clerk was already implemented in the frontend before the preliminary report. Switching to Firebase would have required rewriting the auth layer. Clerk provides the same JWT-based flow with better developer experience and built-in React components. |
| **Inter-service messaging** | Cloud Pub/Sub (async synthesis) | HTTP callbacks (sync POST from synthesis-service to registry-api) | Pub/Sub adds operational complexity (topic/subscription management, dead-letter queues) for a single-producer, single-consumer flow. A direct HTTP callback is simpler, debuggable, and sufficient at current scale. If synthesis volume grows, we can add a message queue without changing the API contract. |
| **Frontend hosting** | Firebase Hosting (static SPA) | In-cluster Next.js Deployment (SSR) | The frontend evolved from a static React SPA to a Next.js 16 App Router application with server-side rendering. Firebase Hosting cannot run SSR; deploying as a Kubernetes Deployment keeps the frontend co-located with the backend for lower latency and simpler networking. |
| **Number of services** | 5 Cloud Run services | 6 Kubernetes Deployments + 1 frontend | Added the MCP Server as a dedicated service and separated the frontend into its own Deployment. |
| **Observability** | Cloud Logging + Cloud Trace + Cloud Monitoring (free tier) | In-cluster Prometheus + Grafana + kube-state-metrics | GCP's managed observability is excellent but the free tier has limited retention and query capabilities. Self-hosted Prometheus/Grafana gives unlimited retention (within PVC bounds), custom dashboards, and zero incremental cost. |
| **Database migrations** | Runtime `create_all()` | Alembic migrations via init container | `create_all()` is unsafe for schema evolution --- it silently ignores column changes and cannot handle renames or deletions. Alembic migrations run in an init container before the application starts, ensuring the schema is always at the expected version. |
| **Chat-backend scaling** | Implied horizontal scaling | Single replica with Redis leader lock | The chat-backend holds in-memory SSE queues and DAG execution state. Running multiple replicas would silently split traffic and break streams. The leader lock makes this constraint explicit and enforced. |

---

## 6. Cost Analysis

### 6.1 Actual Monthly Cost Breakdown

| Resource | Specification | Monthly Cost (USD) |
|----------|-------------|-------------------|
| GKE control plane | Standard tier (zonal) | $0 (free) |
| Main node | 1x `e2-standard-2` (2 vCPU, 8 GB, 50 GB `pd-balanced`) | ~$49 |
| Burst node pool | 0x `e2-small` (spot, scale-to-zero) | $0 (idle) |
| GCE Ingress (HTTP LB) | 1 forwarding rule + traffic | ~$18 |
| Artifact Registry | ~6 images x 2 tags | ~$1 |
| GCS (tools bucket) | <1 GB, versioned | ~$0.03 |
| GCS (backups bucket) | <1 GB, 14-day lifecycle | ~$0.03 |
| Static IP | 1 global address (in use) | $0 (free while attached) |
| Egress | Minimal (demo traffic) | ~$1 |
| **Total** | | **~$70/month** |

### 6.2 Comparison with Preliminary Estimate

| Component | Preliminary Estimate | Actual Cost | Delta |
|-----------|---------------------|-------------|-------|
| Compute | ~$28/mo (Cloud Run, pay-per-request) | ~$49/mo (GKE e2-standard-2, always-on) | +$21 |
| Frontend + Auth | $0 (Firebase free tier) | $0 (Clerk free tier + in-cluster Next.js) | $0 |
| Database | ~$8/mo (2x Cloud SQL shared-core) | ~$0.40/mo (in-cluster PVC) | -$7.60 |
| Object Storage | ~$1/mo (GCS) | ~$0.06/mo (GCS) | -$0.94 |
| Pub/Sub | $0 (free tier) | $0 (HTTP callbacks, no Pub/Sub) | $0 |
| Observability | $0 (GCP free tier) | $0 (in-cluster Prometheus/Grafana) | $0 |
| Load Balancer | Included in Cloud Run | ~$18/mo (GCE Ingress) | +$18 |
| CI/CD + Registry | ~$1/mo | ~$1/mo | $0 |
| **Total** | **~$38/month** | **~$70/month** | **+$32** |

The primary cost increase comes from two factors: (1) GKE's always-on node versus Cloud Run's pay-per-request model, and (2) the GCE HTTP load balancer required for Ingress. The database cost decreased significantly by moving from Cloud SQL to an in-cluster StatefulSet. The net increase of $32/month is acceptable given the benefits of full Kubernetes control (NetworkPolicies, custom observability, init containers, leader locks) that would not be available on Cloud Run.

### 6.3 Budget Management

- **Total credits:** $380 (GCP education credits)
- **Steady-state burn rate:** ~$70/month
- **Estimated runway:** ~5.4 months (sufficient for the course duration)
- **Budget alerts:** Terraform provisions alerts at 16.7% ($50), 50% ($150), 83.3% ($250), and 100% ($300) of a $300 threshold via `google_billing_budget`.

---

## 7. Evaluation

### 7.1 Functional Evaluation

**Tool synthesis.** The system successfully synthesises tools from natural language descriptions. The registry contains 40 tools spanning categories including finance (`stock_quote`, `crypto_price`, `currency_convert`), information retrieval (`arxiv_fetcher`, `wikipedia_search`, `web_search`), media (`image_generate`, `youtube_transcript`, `lyrics_fetch`), and communication (`send_email`, `slack_message`). Each tool follows the spec-driven format: a `spec.yaml` defining inputs, outputs, and compiler targets, plus a Python implementation file.

**MCP protocol compliance.** The MCP Server implements the streamable-http transport with `tools/list`, `tools/call`, and `notifications/tools/list_changed`. MCP-compatible clients (Claude Desktop, Cursor) can connect to the endpoint and immediately discover all 40 tools. Dynamic tool updates propagate to connected clients via list-changed notifications.

**Chat demo.** The React Chat UI accepts natural language queries, displays the DAG execution graph in real time via SSE, and renders final results. Multi-step queries (e.g., "Compare the weather in Singapore and Tokyo and convert the temperature difference to Fahrenheit") correctly decompose into parallel subtasks with dependency resolution.

### 7.2 Non-Functional Evaluation

**Automated testing.** The test suite comprises 201 tests across all six Python packages, covering handler logic, tool creation semantics, environment variable safety, graph flow execution, health endpoints, and more. All tests pass in CI (1.82 seconds on the latest run).

| Package | Test Count | Coverage Areas |
|---------|-----------|----------------|
| mcp_server | 106 | Handlers, creation semantics, env var safety, OAuth, provider, store |
| chat_backend | 39 | Graph flow, planner, trivial fast-path, health |
| registry_api | 24 | Loader, main endpoints, semantic search |
| shared | 18 | CORS, httpx client, rate limiting, request ID |
| synthesis_service | 14 | Prompt builder, health |

**Deployment automation.** Every push to `main` triggers the full pipeline: lint, test, build (6 images in parallel), deploy via Tanka, and rollout verification. The pipeline completes in approximately 8 minutes. Zero manual steps are required after merging a pull request.

**Observability.** Prometheus scrapes application metrics every 15 seconds. Grafana dashboards display request rates, latency distributions, and error rates per service. kube-state-metrics provides cluster-level signals (pod restart counts, resource utilisation).

### 7.3 Scalability

**Horizontal Pod Autoscaler (HPA).** The Kubernetes manifests support HPA configuration for stateless services (registry-api, mcp-server, synthesis-service, registry-ui). The chat-backend is explicitly limited to a single replica due to its in-memory state, enforced by the Redis leader lock.

**Burst node pool.** The Terraform configuration provisions an autoscaling node pool (0--3 `e2-small` spot instances) with a `pool=burst` taint. Workloads that tolerate the burst taint can schedule onto these nodes during traffic spikes, scaling back to zero when idle.

**Scaling constraints.** Two components limit horizontal scaling:
1. **chat-backend**: In-memory SSE queues and DAG state require single-replica deployment. Migrating to Redis-backed event queues would remove this constraint but adds complexity.
2. **PostgreSQL**: The in-cluster StatefulSet is a single-replica deployment with no read replicas. For production scale, migrating to Cloud SQL with read replicas or a managed PostgreSQL service would be necessary.

---

## 8. Implementation Timeline

The project spanned 7 weeks from initial commit (1 March 2026) to final deployment (16 April 2026). The actual timeline diverged from the preliminary plan due to the shift from Cloud Run to GKE and the iterative nature of infrastructure debugging.

| Week | Dates | Deliverables |
|------|-------|-------------|
| 1 | 1--7 Mar | Initial monorepo scaffolding (Babel). Docker-compose development environment. Registry API with CRUD, full-text search. Chat UI and Registry UI prototypes. Clerk authentication integration across frontend and backend. |
| 2 | 8--14 Mar | SQLAlchemy 2.0 async database layer (PostgreSQL + SQLite fallback). Health checks across all services. Structured logging. Tool statistics endpoint. Synthesis pipeline debugging. |
| 3 | 15--21 Mar | Rebrand from Babel to Kiln. Nx monorepo restructure. |
| 4 | 22--28 Mar | MCP Server implementation (JSON-RPC bridge, OAuth 2.1 provider, Clerk callback). 11 new keyless tools. Next.js rebuild (from Vite SPA to App Router with SSR). PyPI packaging. |
| 5 | 29 Mar--4 Apr | Chat rewrite with Vercel AI SDK. DAG visualisation. Tool Executor service stub. Major UI polish (glass-morphism navbar, dark theme). CORS consolidation into shared module. Mistral 429 retry logic. |
| 6 | 5--11 Apr | Landing page redesign. Semantic tool search (BM25 index + Mistral embeddings). MCP tool creation via client-LLM-authored specs. Env var safety analysis (AST-based detection, allowlist, sandbox contract). |
| 7 | 12--16 Apr | GKE deployment infrastructure: Terraform (VPC, GKE, IAM, GCS, budget alerts), Tanka/Jsonnet (33 K8s resources), GitHub Actions CI/CD pipeline. Alembic migrations with init containers. Redis leader lock. Production debugging (Dockerfile MODULE bug, CPU pressure, secret management). |

---

## 9. Lessons Learned

### 9.1 Infrastructure Debugging Is Iterative

The first GKE deployment required three rounds of fixes (PRs #30, #31, #32) before all pods were healthy. Issues encountered:

- **Dockerfile `MODULE` variable expansion.** The `CMD` instruction used `${MODULE}` which was evaluated at build time (empty) rather than runtime. Fixed by wrapping in `sh -c`.
- **DATABASE_URL percent-encoding.** Alembic's `env.py` interprets `%` as a Python format string substitution. Passwords containing `%` caused silent connection failures. Fixed by escaping `%` as `%%` in the Alembic configuration.
- **Selector label immutability.** Changing a Deployment's `spec.selector.matchLabels` after creation is rejected by the Kubernetes API. Required deleting and recreating affected Deployments.
- **ConfigMap volume mounts for Grafana.** The initial manifest used `emptyDir` for dashboard storage but forgot to mount the provisioning ConfigMaps. Grafana started with no datasources configured.

### 9.2 Cost-Conscious Architecture Requires Different Trade-offs

The preliminary report assumed Cloud Run's pay-per-request model would minimise costs. In practice, GKE Standard with a single node proved more cost-effective for a workload that runs continuously (the registry must be always available for MCP clients). The major cost surprise was the $18/month GCE load balancer --- a fixed cost that does not exist in Cloud Run (where load balancing is included). If starting over, we would evaluate using a NodePort service with a simple cloud NAT to avoid the LB cost entirely.

### 9.3 In-Cluster vs Managed Services

The decision to run PostgreSQL and Prometheus in-cluster rather than using Cloud SQL and Cloud Monitoring was driven primarily by cost. At demo scale, the operational burden is minimal --- but it comes with real trade-offs:

- **No automatic failover.** If the Postgres pod crashes, there is brief downtime while Kubernetes restarts it. Cloud SQL provides automatic failover with a standby replica.
- **Manual backups.** We rely on periodic `pg_dump` to GCS rather than Cloud SQL's continuous backup with point-in-time recovery.
- **No cross-zone durability.** The PVC is zonal. A zone failure would require restoring from the GCS backup.

For a production deployment, Cloud SQL would be the correct choice. For a course project with $380 in credits and five months of runway, the in-cluster approach saves ~$16/month (two Cloud SQL instances) without material risk.

---

## 10. Conclusion

Kiln demonstrates that a self-evolving tool registry for AI agents can be deployed as a cost-effective cloud-native application on Google Cloud Platform. The system serves 40 tools to any MCP-compatible AI assistant, synthesises new tools on demand via LLM code generation, and provides a chat interface with real-time DAG execution visualisation.

The final architecture --- six microservices on GKE Standard with in-cluster PostgreSQL, Redis, Prometheus, and Grafana --- diverges significantly from the preliminary Cloud Run proposal. This shift was driven by practical constraints: the need for init containers (Alembic migrations), NetworkPolicies (synthesis sandboxing), and custom observability, all of which are unavailable or more complex on Cloud Run. The trade-off is a higher steady-state cost ($70/month vs the projected $38/month) offset by greater operational control and simpler debugging.

The infrastructure is fully codified: 342 lines of Terraform, 514 lines of Tanka/Jsonnet, and a GitHub Actions pipeline that takes code from commit to verified deployment in approximately 8 minutes. 201 automated tests provide confidence that deployments do not regress functionality. Budget alerts at multiple thresholds ensure the $380 credit allocation is managed responsibly.

The project validates two key claims: (1) LLM-powered tool synthesis can extend an AI agent's capabilities at runtime without human intervention, and (2) a microservices architecture on managed Kubernetes provides the isolation, observability, and deployment automation needed to operate such a system reliably in the cloud.

---

## 11. References

[1] Anthropic, "Model Context Protocol Specification v2025-11-25," modelcontextprotocol.io, Nov 2025.

[2] Google Cloud, "Google Kubernetes Engine Documentation," cloud.google.com/kubernetes-engine/docs, 2025.

[3] Google Cloud, "Terraform on Google Cloud," cloud.google.com/docs/terraform, 2025.

[4] Grafana Labs, "Tanka --- Flexible, Reusable and Concise Configuration for Kubernetes," tanka.dev, 2025.

[5] Google Cloud, "Artifact Registry Documentation," cloud.google.com/artifact-registry/docs, 2025.

[6] Google Cloud, "Workload Identity Federation," cloud.google.com/iam/docs/workload-identity-federation, 2025.

[7] Prometheus Authors, "Prometheus --- Monitoring System and Time Series Database," prometheus.io, 2025.

[8] Grafana Labs, "Grafana Documentation," grafana.com/docs/grafana, 2025.

[9] Clerk, "Clerk Documentation," clerk.com/docs, 2025.

[10] Nrwl, "Nx: Smart, Fast, Extensible Build System," nx.dev, 2025.

[11] Google Cloud, "Cloud Storage Documentation," cloud.google.com/storage/docs, 2025.

[12] SQLAlchemy Authors, "SQLAlchemy 2.0 Documentation," docs.sqlalchemy.org, 2025.

[13] Alembic Authors, "Alembic Documentation," alembic.sqlalchemy.org, 2025.

[14] Microsoft, "AG2 (AutoGen) Documentation," ag2.ai, 2025.

[15] Mistral AI, "Mistral API Documentation," docs.mistral.ai, 2025.
