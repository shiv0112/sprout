terraform {
  backend "gcs" {
    bucket = "peaceful-basis-329822-tfstate"
    prefix = "env/dev"
  }
}
