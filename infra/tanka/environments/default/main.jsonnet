local sprout = import 'sprout.libsonnet';

sprout {
  _config+:: {
    namespace: 'sprout',
    image_registry: std.extVar('IMAGE_REGISTRY'),
    image_tag: std.extVar('IMAGE_TAG'),
    gcs_bucket: std.extVar('GCS_BUCKET'),
    workload_sa: std.extVar('WORKLOAD_SA'),
    ingress_ip: std.extVar('INGRESS_IP'),
    ingress_ip_name: std.extVar('INGRESS_IP_NAME'),
    pg_backup_bucket: std.extVar('PG_BACKUP_BUCKET'),
  },
}
