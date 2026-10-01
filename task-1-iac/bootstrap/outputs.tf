output "state_bucket" {
  value = aws_s3_bucket.state.id
}

output "lock_table" {
  value = aws_dynamodb_table.lock.name
}

output "backend_hcl" {
  description = "Paste into ../backend.hcl (git-ignored)."
  value       = <<-EOT
    bucket         = "${aws_s3_bucket.state.id}"
    key            = "${var.project}/terraform.tfstate"
    region         = "${var.aws_region}"
    dynamodb_table = "${aws_dynamodb_table.lock.name}"
    encrypt        = true
  EOT
}
