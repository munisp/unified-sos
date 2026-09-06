output "bucket_names" {
  description = "Map of bucket purpose to bucket name."
  value       = { for purpose, bucket in aws_s3_bucket.state : purpose => bucket.bucket }
}

output "bucket_arns" {
  description = "Map of bucket purpose to bucket ARN."
  value       = { for purpose, bucket in aws_s3_bucket.state : purpose => bucket.arn }
}
