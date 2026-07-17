local k = import 'k.libsonnet';

{
  local container = k.core.v1.container,
  local deployment = k.apps.v1.deployment,
  local service = k.core.v1.service,

  local redisContainer =
    container.new('redis', 'redis:7-alpine')
    + container.withPorts([k.core.v1.containerPort.new(6379)])
    + container.withArgs(['--maxmemory', '128mb', '--maxmemory-policy', 'allkeys-lru', '--save', ''])
    + container.resources.withRequests({ cpu: '50m', memory: '64Mi' })
    + container.resources.withLimits({ cpu: '200m', memory: '192Mi' })
    + container.livenessProbe.exec.withCommand(['redis-cli', 'ping'])
    + container.livenessProbe.withInitialDelaySeconds(5)
    + container.livenessProbe.withPeriodSeconds(10)
    + container.readinessProbe.exec.withCommand(['redis-cli', 'ping'])
    + container.readinessProbe.withInitialDelaySeconds(3)
    + container.readinessProbe.withPeriodSeconds(5),

  redis_deployment:
    deployment.new('redis', replicas=1, containers=[redisContainer])
    + deployment.metadata.withNamespace($._config.namespace)
    + deployment.spec.template.metadata.withLabels({ app: 'redis' })
    + deployment.spec.selector.withMatchLabels({ app: 'redis' }),

  redis_service:
    service.new('redis', { app: 'redis' }, [{ port: 6379, targetPort: 6379 }])
    + service.metadata.withNamespace($._config.namespace),
}
