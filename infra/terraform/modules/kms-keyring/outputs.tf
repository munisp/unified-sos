output "key_arns" {
  description = "Map of key purpose to KMS key ARN."
  value       = { for purpose, key in aws_kms_key.state : purpose => key.arn }
}

output "keyring_alias_prefix" {
  description = "Alias prefix for this state keyring (alias/sos-<state>-*)."
  value       = "alias/sos-${var.state_id}"
}
