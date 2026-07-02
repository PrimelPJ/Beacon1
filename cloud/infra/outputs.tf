output "iot_endpoint_hint" {
  value       = "run: aws iot describe-endpoint --endpoint-type iot:Data-ATS"
  description = "IoT Core data endpoint is account and region specific."
}
output "kinesis_stream"   { value = aws_kinesis_stream.telemetry.name }
output "dynamodb_table"   { value = aws_dynamodb_table.telemetry.name }
output "cold_bucket"      { value = aws_s3_bucket.cold.bucket }
output "alert_topic_arn"  { value = aws_sns_topic.alerts.arn }
output "robot_policy_name"{ value = aws_iot_policy.robot.name }
