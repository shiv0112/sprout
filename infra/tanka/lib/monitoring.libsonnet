local k = import 'k.libsonnet';

local container = k.core.v1.container;
local deployment = k.apps.v1.deployment;
local statefulSet = k.apps.v1.statefulSet;
local service = k.core.v1.service;
local configMap = k.core.v1.configMap;
local pvc = k.core.v1.persistentVolumeClaim;
local volume = k.core.v1.volume;
local volumeMount = k.core.v1.volumeMount;

{
  prometheus_config:
    configMap.new('prometheus-config', {
      'prometheus.yml': |||
        global:
          scrape_interval: 15s

        scrape_configs:
          - job_name: 'kubernetes-pods'
            kubernetes_sd_configs:
              - role: pod
                namespaces:
                  names: ['%s']
            relabel_configs:
              - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
                action: keep
                regex: 'true'
              - source_labels: [__meta_kubernetes_pod_ip, __meta_kubernetes_pod_annotation_prometheus_io_port]
                action: replace
                regex: (.+);(.+)
                replacement: '$1:$2'
                target_label: __address__
              - source_labels: [__meta_kubernetes_pod_label_app]
                action: replace
                target_label: service

          - job_name: 'kube-state-metrics'
            static_configs:
              - targets: ['kube-state-metrics:8080']
      ||| % $._config.namespace,
    })
    + configMap.metadata.withNamespace($._config.namespace),

  prometheus_statefulset:
    local promContainer =
      container.new('prometheus', 'prom/prometheus:v2.53.0')
      + container.withArgs([
        '--config.file=/etc/prometheus/prometheus.yml',
        '--storage.tsdb.path=/prometheus',
        '--storage.tsdb.retention.time=7d',
        '--storage.tsdb.retention.size=2GB',
        '--web.enable-lifecycle',
      ])
      + container.withPorts([k.core.v1.containerPort.new(9090)])
      + container.withVolumeMountsMixin([
        volumeMount.new('config', '/etc/prometheus'),
        volumeMount.new('data', '/prometheus'),
      ])
      + container.resources.withRequests({ cpu: '100m', memory: '256Mi' })
      + container.resources.withLimits({ cpu: '500m', memory: '512Mi' })
      + container.readinessProbe.httpGet.withPath('/-/ready')
      + container.readinessProbe.httpGet.withPort(9090)
      + container.readinessProbe.withInitialDelaySeconds(5);

    local promPvc =
      pvc.new('data')
      + pvc.spec.withAccessModes(['ReadWriteOnce'])
      + pvc.spec.withStorageClassName('premium-rwo')
      + pvc.spec.resources.withRequests({ storage: '5Gi' });

    statefulSet.new('prometheus', replicas=1, containers=[promContainer], volumeClaims=[promPvc])
    + statefulSet.metadata.withNamespace($._config.namespace)
    + statefulSet.spec.withServiceName('prometheus')
    + statefulSet.spec.template.spec.withServiceAccountName('sprout-sa')
    + statefulSet.spec.template.spec.securityContext.withFsGroup(65534)
    + statefulSet.spec.template.spec.securityContext.withRunAsUser(65534)
    + statefulSet.spec.template.spec.withVolumesMixin([
      volume.fromConfigMap('config', 'prometheus-config'),
    ]),

  prometheus_service:
    service.new('prometheus', { name: 'prometheus' }, [{ port: 9090, targetPort: 9090 }])
    + service.metadata.withNamespace($._config.namespace),

  kube_state_metrics_deployment:
    local ksmContainer =
      container.new('kube-state-metrics', 'registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.12.0')
      + container.withPorts([
        k.core.v1.containerPort.new(8080),
        k.core.v1.containerPort.new(8081),
      ])
      + container.resources.withRequests({ cpu: '10m', memory: '32Mi' })
      + container.resources.withLimits({ cpu: '100m', memory: '128Mi' });

    deployment.new('kube-state-metrics', replicas=1, containers=[ksmContainer])
    + deployment.metadata.withNamespace($._config.namespace)
    + deployment.spec.template.spec.withServiceAccountName('sprout-sa'),

  kube_state_metrics_service:
    service.new('kube-state-metrics', { name: 'kube-state-metrics' }, [{ port: 8080, targetPort: 8080 }])
    + service.metadata.withNamespace($._config.namespace),

  grafana_datasources_configmap:
    configMap.new('grafana-datasources', {
      'datasources.yaml': |||
        apiVersion: 1
        datasources:
          - name: Prometheus
            type: prometheus
            url: http://prometheus:9090
            access: proxy
            isDefault: true
      |||,
    })
    + configMap.metadata.withNamespace($._config.namespace),

  grafana_dashboards_provider_configmap:
    configMap.new('grafana-dashboards-provider', {
      'dashboards.yaml': |||
        apiVersion: 1
        providers:
          - name: default
            folder: ''
            type: file
            options:
              path: /var/lib/grafana/dashboards
      |||,
    })
    + configMap.metadata.withNamespace($._config.namespace),

  grafana_deployment:
    local grafanaContainer =
      container.new('grafana', 'grafana/grafana:11.1.0')
      + container.withPorts([k.core.v1.containerPort.new(3001)])
      + container.withEnvMixin([
        k.core.v1.envVar.new('GF_SERVER_HTTP_PORT', '3001'),
        k.core.v1.envVar.fromSecretRef('GF_SECURITY_ADMIN_PASSWORD', 'sprout-secrets', 'GRAFANA_PASSWORD'),
      ])
      + container.withVolumeMountsMixin([
        volumeMount.new('datasources', '/etc/grafana/provisioning/datasources'),
        volumeMount.new('dashboards-provider', '/etc/grafana/provisioning/dashboards'),
        volumeMount.new('dashboards', '/var/lib/grafana/dashboards'),
      ])
      + container.resources.withRequests({ cpu: '50m', memory: '128Mi' })
      + container.resources.withLimits({ cpu: '250m', memory: '256Mi' });

    deployment.new('grafana', replicas=1, containers=[grafanaContainer])
    + deployment.metadata.withNamespace($._config.namespace)
    + deployment.spec.template.spec.withVolumesMixin([
      volume.fromConfigMap('datasources', 'grafana-datasources'),
      volume.fromConfigMap('dashboards-provider', 'grafana-dashboards-provider'),
      volume.withName('dashboards') + volume.emptyDir.withMedium(''),
    ]),

  grafana_backend_config: {
    apiVersion: 'cloud.google.com/v1',
    kind: 'BackendConfig',
    metadata: {
      name: 'grafana',
      namespace: $._config.namespace,
    },
    spec: {
      healthCheck: {
        checkIntervalSec: 15,
        timeoutSec: 5,
        port: 3001,
        type: 'HTTP',
        requestPath: '/api/health',
      },
    },
  },

  grafana_service:
    service.new('grafana', { name: 'grafana' }, [{ port: 3001, targetPort: 3001 }])
    + service.metadata.withNamespace($._config.namespace)
    + service.metadata.withAnnotationsMixin({
      'cloud.google.com/backend-config': '{"default": "grafana"}',
    }),
}
