# ---- packaging ----------------------------------------------------------------
data "archive_file" "processor" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/telemetry_processor"
  output_path = "${path.module}/build/telemetry_processor.zip"
}

data "archive_file" "alarm" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/alarm_evaluator"
  output_path = "${path.module}/build/alarm_evaluator.zip"
}

# ---- hot-path processor -------------------------------------------------------
resource "aws_lambda_function" "processor" {
  function_name    = "${var.project}-telemetry-processor"
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.processor.output_path
  source_code_hash = data.archive_file.processor.output_base64sha256
  role             = aws_iam_role.lambda.arn
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      TABLE_NAME       = aws_dynamodb_table.telemetry.name
      METRIC_NAMESPACE = "Beacon/Fleet"
      ALERT_TOPIC_ARN  = aws_sns_topic.alerts.arn
      LOW_BATTERY_PCT  = "20"
      STALE_ODOM_S     = "5"
    }
  }
}

resource "aws_lambda_event_source_mapping" "kinesis" {
  event_source_arn                   = aws_kinesis_stream.telemetry.arn
  function_name                      = aws_lambda_function.processor.arn
  starting_position                  = "LATEST"
  batch_size                         = 100
  maximum_batching_window_in_seconds = 5
  # A poison record should not wedge the shard. Split the batch and give up
  # after a bounded number of retries, then move on.
  bisect_batch_on_function_error = true
  maximum_retry_attempts         = 5
}

# ---- scheduled offline sweep --------------------------------------------------
resource "aws_lambda_function" "alarm" {
  function_name    = "${var.project}-alarm-evaluator"
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.alarm.output_path
  source_code_hash = data.archive_file.alarm.output_base64sha256
  role             = aws_iam_role.lambda.arn
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      TABLE_NAME          = aws_dynamodb_table.telemetry.name
      ALERT_TOPIC_ARN     = aws_sns_topic.alerts.arn
      OFFLINE_THRESHOLD_S = "30"
    }
  }
}

resource "aws_cloudwatch_event_rule" "every_minute" {
  name                = "${var.project}-offline-sweep"
  schedule_expression = "rate(1 minute)"
}

resource "aws_cloudwatch_event_target" "alarm" {
  rule = aws_cloudwatch_event_rule.every_minute.name
  arn  = aws_lambda_function.alarm.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.alarm.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.every_minute.arn
}

# ---- alerts -------------------------------------------------------------------
resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ---- IAM for both functions ---------------------------------------------------
resource "aws_iam_role" "lambda" {
  name = "${var.project}-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda" {
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow",
        Action = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"],
        Resource = aws_dynamodb_table.telemetry.arn },
      { Effect = "Allow",
        Action = ["kinesis:GetRecords", "kinesis:GetShardIterator", "kinesis:DescribeStream",
                  "kinesis:ListShards", "kinesis:ListStreams"],
        Resource = aws_kinesis_stream.telemetry.arn },
      { Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*" },
      { Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.alerts.arn }
    ]
  })
}
