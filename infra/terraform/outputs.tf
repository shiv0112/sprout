output "gke_cluster_name" {
  value = google_container_cluster.sprout.name
}

output "gke_cluster_endpoint" {
  value     = google_container_cluster.sprout.endpoint
  sensitive = true
}

output "gke_cluster_zone" {
  value = var.zone
}

output "artifact_registry_url" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.sprout.repository_id}"
}

output "gcs_tools_bucket" {
  value = google_storage_bucket.tools.name
}

output "gcs_pg_backups_bucket" {
  value = google_storage_bucket.pg_backups.name
}

output "workload_sa_email" {
  value = google_service_account.sprout_workload.email
}

output "ci_sa_email" {
  value = google_service_account.github_ci.email
}

output "ingress_static_ip" {
  value = google_compute_global_address.ingress_ip.address
}

output "nip_io_domain" {
  value = "sprout.${google_compute_global_address.ingress_ip.address}.nip.io"
}

output "gke_get_credentials" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.sprout.name} --zone ${var.zone} --project ${var.project_id}"
}

output "wif_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}
