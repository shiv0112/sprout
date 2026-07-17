local k = import 'k.libsonnet';
local postgres = import 'postgres.libsonnet';
local redis = import 'redis.libsonnet';
local netpol = import 'networkpolicies.libsonnet';
local monitoring = import 'monitoring.libsonnet';

{
  _config:: {
    namespace: 'kiln',
    image_registry: error 'must set _config.image_registry',
    image_tag: 'latest',
    gcs_bucket: error 'must set _config.gcs_bucket',
    workload_sa: error 'must set _config.workload_sa',
    ingress_ip: error 'must set _config.ingress_ip',
    ingress_ip_name: error 'must set _config.ingress_ip_name',
    pg_backup_bucket: error 'must set _config.pg_backup_bucket',

    ports: {
      registry_api: 8766,
      chat_backend: 8765,
      tool_executor: 8767,
      mcp_server: 8768,
      synthesis_service: 8002,
      registry_ui: 3000,
    },
  },

  namespace: k.core.v1.namespace.new($._config.namespace),

  service_account:
    k.core.v1.serviceAccount.new('kiln-sa')
    + k.core.v1.serviceAccount.metadata.withNamespace($._config.namespace)
    + k.core.v1.serviceAccount.metadata.withAnnotations({
      'iam.gke.io/gcp-service-account': $._config.workload_sa,
    }),

  configmap:
    k.core.v1.configMap.new('kiln-config', {
      KILN_ENV: 'prod',
      REDIS_URL: 'redis://redis:6379/0',
      KILN_REGISTRY_URL: 'http://registry-api:%(registry_api)d' % $._config.ports,
      REGISTRY_URL: 'http://registry-api:%(registry_api)d' % $._config.ports,
      KILN_SYNTHESIS_URL: 'http://synthesis-service:%(synthesis_service)d' % $._config.ports,
      SYNTHESIS_URL: 'http://synthesis-service:%(synthesis_service)d' % $._config.ports,
      KILN_CALLBACK_URL: 'http://registry-api:%(registry_api)d/synthesis/callback' % $._config.ports,
      KILN_SYNTHESIS_CALLBACK_URL: 'http://registry-api:%(registry_api)d/synthesis/callback' % $._config.ports,
      KILN_MCP_ISSUER_URL: 'https://mcp.%s.sslip.io' % $._config.ingress_ip,
      KILN_MCP_HOST: '0.0.0.0',
      KILN_MCP_ALLOW_HTTP_ISSUER: 'true',
      KILN_UI_URL: 'https://kiln.%s.sslip.io' % $._config.ingress_ip,
      TOOL_EXECUTOR_URL: 'http://tool-executor:%(tool_executor)d' % $._config.ports,
      GCS_BUCKET: $._config.gcs_bucket,
      CORS_ORIGINS: 'http://kiln.%s.sslip.io,https://kiln.%s.sslip.io' % [$._config.ingress_ip, $._config.ingress_ip],
    })
    + k.core.v1.configMap.metadata.withNamespace($._config.namespace),

  local database_url = 'postgresql://kiln:$(PG_PASSWORD)@postgres:5432/kiln_registry',

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
        + container.withEnvMixin([
          k.core.v1.envVar.fromFieldPath('POD_NAME', 'metadata.name'),
          k.core.v1.envVar.new('DATABASE_URL', database_url),
        ])
        + container.resources.withRequests({
          cpu: std.get(args, 'cpu_request', '250m'),
          memory: std.get(args, 'memory_request', '256Mi'),
        })
        + container.resources.withLimits({
          cpu: std.get(args, 'cpu_limit', '500m'),
          memory: std.get(args, 'memory_limit', '512Mi'),
        })
        + container.livenessProbe.httpGet.withPath('/livez')
        + container.livenessProbe.httpGet.withPort(port)
        + container.livenessProbe.withInitialDelaySeconds(10)
        + container.livenessProbe.withPeriodSeconds(15)
        + container.readinessProbe.httpGet.withPath('/readyz')
        + container.readinessProbe.httpGet.withPort(port)
        + container.readinessProbe.withInitialDelaySeconds(5)
        + container.readinessProbe.withPeriodSeconds(5),
      ])
      + deployment.metadata.withNamespace($._config.namespace)
      + (
        if std.get(args, 'prometheus_scrape', true) then
          deployment.spec.template.metadata.withAnnotationsMixin({
            'prometheus.io/scrape': 'true',
            'prometheus.io/port': '%d' % port,
            'prometheus.io/path': '/metrics',
          })
        else {}
      )
      + deployment.spec.template.spec.withServiceAccountName('kiln-sa'),

    backend_config: {
      apiVersion: 'cloud.google.com/v1',
      kind: 'BackendConfig',
      metadata: {
        name: name,
        namespace: $._config.namespace,
      },
      spec: {
        healthCheck: {
          checkIntervalSec: 15,
          timeoutSec: 5,
          port: port,
          type: 'HTTP',
          requestPath: std.get(args, 'health_path', '/livez'),
        },
      },
    },

    service:
      service.new(name, { name: name }, [{ port: port, targetPort: port }])
      + service.metadata.withNamespace($._config.namespace)
      + service.metadata.withAnnotationsMixin({
        'cloud.google.com/backend-config': '{"default": "%s"}' % name,
      }),
  },

  registry_api: kilnService('registry-api', $._config.ports.registry_api, 'registry-api', {
    cpu_request: '100m', memory_request: '256Mi',
    cpu_limit: '1000m', memory_limit: '512Mi',
  }) {
    deployment+:
      k.apps.v1.deployment.spec.template.spec.withInitContainers([
        k.core.v1.container.new('migrate', '%s/%s:%s' % [$._config.image_registry, 'registry-api', $._config.image_tag])
        + k.core.v1.container.withCommand(['kiln-registry', 'migrate'])
        + k.core.v1.container.withEnvFrom([
          k.core.v1.envFromSource.configMapRef.withName('kiln-config'),
          k.core.v1.envFromSource.secretRef.withName('kiln-secrets'),
        ])
        + k.core.v1.container.withEnvMixin([
          k.core.v1.envVar.new('DATABASE_URL', database_url),
        ]),
      ]),
  },

  chat_backend: kilnService('chat-backend', $._config.ports.chat_backend, 'chat-backend', {
    cpu_request: '100m', memory_request: '256Mi',
    cpu_limit: '1000m', memory_limit: '512Mi',
  }) {
    deployment+: {
      spec+: {
        strategy: { type: 'Recreate' },
        template+: {
          spec+: {
            terminationGracePeriodSeconds: 30,
          },
        },
      },
    },
  },

  tool_executor: kilnService('tool-executor', $._config.ports.tool_executor, 'tool-executor', {
    cpu_request: '50m', memory_request: '256Mi',
    cpu_limit: '1000m', memory_limit: '512Mi',
  }),

  mcp_server: kilnService('mcp-server', $._config.ports.mcp_server, 'mcp-server', {
    cpu_request: '50m', memory_request: '128Mi',
    cpu_limit: '250m', memory_limit: '256Mi',
  }) {
    deployment+: {
      spec+: {
        template+: {
          spec+: {
            containers: [
              super.containers[0]
              + k.core.v1.container.withCommand(['python', '-m', 'kiln_mcp.main', 'streamable-http']),
            ],
          },
        },
      },
    },
  },

  synthesis_service: kilnService('synthesis-service', $._config.ports.synthesis_service, 'synthesis-service', {
    cpu_request: '100m', memory_request: '512Mi',
    cpu_limit: '2000m', memory_limit: '1Gi',
  }),

  registry_ui: kilnService('registry-ui', $._config.ports.registry_ui, 'registry-ui', {
    cpu_request: '50m', memory_request: '128Mi',
    cpu_limit: '500m', memory_limit: '256Mi',
    prometheus_scrape: false,
    health_path: '/',
  }) {
    deployment+: {
      spec+: {
        template+: {
          spec+: {
            containers: [
              super.containers[0]
              + k.core.v1.container.withImagePullPolicy('Always')
              + k.core.v1.container.withEnvMixin([
                k.core.v1.envVar.new('HOSTNAME', '0.0.0.0'),
              ])
              + k.core.v1.container.livenessProbe.httpGet.withPath('/')
              + k.core.v1.container.readinessProbe.httpGet.withPath('/'),
            ],
          },
        },
      },
    },
  },

  ingress:
    k.networking.v1.ingress.new('kiln-ingress')
    + k.networking.v1.ingress.metadata.withNamespace($._config.namespace)
    + k.networking.v1.ingress.metadata.withAnnotations({
      'kubernetes.io/ingress.class': 'nginx',
      'cert-manager.io/cluster-issuer': 'letsencrypt-prod',
      'kubernetes.io/ingress.allow-http': 'true',
    })
    + k.networking.v1.ingress.spec.withTls([{
      hosts: ['kiln.%s.sslip.io' % $._config.ingress_ip],
      secretName: 'kiln-tls',
    }])
    + k.networking.v1.ingress.spec.withRules([
      {
        host: 'kiln.%s.sslip.io' % $._config.ingress_ip,
        http: { paths: [{
          path: '/',
          pathType: 'Prefix',
          backend: { service: { name: 'registry-ui', port: { number: $._config.ports.registry_ui } } },
        }] },
      },
      {
        host: 'api.%s.sslip.io' % $._config.ingress_ip,
        http: { paths: [{
          path: '/',
          pathType: 'Prefix',
          backend: { service: { name: 'registry-api', port: { number: $._config.ports.registry_api } } },
        }] },
      },
      {
        host: 'chat.%s.sslip.io' % $._config.ingress_ip,
        http: { paths: [{
          path: '/',
          pathType: 'Prefix',
          backend: { service: { name: 'chat-backend', port: { number: $._config.ports.chat_backend } } },
        }] },
      },
      {
        host: 'mcp.%s.sslip.io' % $._config.ingress_ip,
        http: { paths: [{
          path: '/',
          pathType: 'Prefix',
          backend: { service: { name: 'mcp-server', port: { number: $._config.ports.mcp_server } } },
        }] },
      },
      {
        host: 'grafana.%s.sslip.io' % $._config.ingress_ip,
        http: { paths: [{
          path: '/',
          pathType: 'Prefix',
          backend: { service: { name: 'grafana', port: { number: 3001 } } },
        }] },
      },
    ]),
} + postgres + redis + netpol + monitoring
