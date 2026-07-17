local k = import 'k.libsonnet';

{
  local pvc = k.core.v1.persistentVolumeClaim,
  local container = k.core.v1.container,
  local volumeMount = k.core.v1.volumeMount,
  local statefulSet = k.apps.v1.statefulSet,
  local service = k.core.v1.service,

  local pgContainer =
    container.new('postgres', 'postgres:17-alpine')
    + container.withPorts([k.core.v1.containerPort.new(5432)])
    + container.withEnvMixin([
      k.core.v1.envVar.new('POSTGRES_DB', 'kiln_registry'),
      k.core.v1.envVar.new('POSTGRES_USER', 'kiln'),
      k.core.v1.envVar.fromSecretRef('POSTGRES_PASSWORD', 'kiln-secrets', 'PG_PASSWORD'),
      k.core.v1.envVar.new('PGDATA', '/var/lib/postgresql/data/pgdata'),
    ])
    + container.resources.withRequests({ cpu: '100m', memory: '256Mi' })
    + container.resources.withLimits({ cpu: '500m', memory: '512Mi' })
    + container.livenessProbe.exec.withCommand(['pg_isready', '-U', 'kiln'])
    + container.livenessProbe.withInitialDelaySeconds(15)
    + container.livenessProbe.withPeriodSeconds(10)
    + container.readinessProbe.exec.withCommand(['pg_isready', '-U', 'kiln'])
    + container.readinessProbe.withInitialDelaySeconds(5)
    + container.readinessProbe.withPeriodSeconds(5)
    + container.withVolumeMountsMixin([volumeMount.new('pgdata', '/var/lib/postgresql/data')]),

  local pgPvc =
    pvc.new('pgdata')
    + pvc.spec.withAccessModes(['ReadWriteOnce'])
    + pvc.spec.withStorageClassName('premium-rwo')
    + pvc.spec.resources.withRequests({ storage: '10Gi' }),

  postgres_statefulset:
    statefulSet.new('postgres', replicas=1, containers=[pgContainer], volumeClaims=[pgPvc])
    + statefulSet.metadata.withNamespace($._config.namespace)
    + statefulSet.spec.withServiceName('postgres')
    + statefulSet.spec.template.metadata.withLabels({ app: 'postgres' })
    + statefulSet.spec.selector.withMatchLabels({ app: 'postgres' }),

  postgres_service:
    service.new('postgres', { app: 'postgres' }, [{ port: 5432, targetPort: 5432 }])
    + service.metadata.withNamespace($._config.namespace)
    + service.spec.withClusterIP('None'),
}
