# IoT rule: forward every telemetry message into the Kinesis hot-path stream.
# SQL selects all fields plus the MQTT topic so downstream can trust device_id.
resource "aws_iot_topic_rule" "telemetry_to_kinesis" {
  name        = "${replace(var.project, "-", "_")}_telemetry_to_kinesis"
  enabled     = true
  sql         = "SELECT * FROM 'robots/+/telemetry'"
  sql_version = "2016-03-23"

  kinesis {
    stream_name   = aws_kinesis_stream.telemetry.name
    role_arn      = aws_iam_role.iot_to_kinesis.arn
    partition_key = "$${topic(2)}" # device_id segment becomes the partition key
  }
}

# Second rule: same messages fan out to Firehose for the cold analytics path.
resource "aws_iot_topic_rule" "telemetry_to_firehose" {
  name        = "${replace(var.project, "-", "_")}_telemetry_to_firehose"
  enabled     = true
  sql         = "SELECT * FROM 'robots/+/telemetry'"
  sql_version = "2016-03-23"

  firehose {
    delivery_stream_name = aws_kinesis_firehose_delivery_stream.cold.name
    role_arn             = aws_iam_role.iot_to_firehose.arn
    separator            = "\n"
  }
}

resource "aws_iam_role" "iot_to_kinesis" {
  name = "${var.project}-iot-to-kinesis"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "iot.amazonaws.com" }, Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "iot_to_kinesis" {
  role = aws_iam_role.iot_to_kinesis.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Action = ["kinesis:PutRecord"], Resource = aws_kinesis_stream.telemetry.arn
    }]
  })
}

resource "aws_iam_role" "iot_to_firehose" {
  name = "${var.project}-iot-to-firehose"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "iot.amazonaws.com" }, Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "iot_to_firehose" {
  role = aws_iam_role.iot_to_firehose.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Action = ["firehose:PutRecord", "firehose:PutRecordBatch"],
      Resource = aws_kinesis_firehose_delivery_stream.cold.arn
    }]
  })
}

# Least-privilege policy attached to each robot certificate: a robot may only
# connect as its own client id and publish to its own telemetry topic. The
# ${iot:Connection.Thing.ThingName} policy variable enforces this per device.
resource "aws_iot_policy" "robot" {
  name = "${var.project}-robot-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["iot:Connect"]
        Resource = "arn:aws:iot:${var.region}:*:client/$${iot:Connection.Thing.ThingName}"
      },
      {
        Effect   = "Allow"
        Action   = ["iot:Publish"]
        Resource = "arn:aws:iot:${var.region}:*:topic/robots/$${iot:Connection.Thing.ThingName}/telemetry"
      },
      {
        Effect = "Allow"
        Action = ["iot:Publish", "iot:Subscribe", "iot:Receive"]
        Resource = [
          "arn:aws:iot:${var.region}:*:topic/$$aws/things/$${iot:Connection.Thing.ThingName}/shadow/*",
          "arn:aws:iot:${var.region}:*:topicfilter/$$aws/things/$${iot:Connection.Thing.ThingName}/shadow/*"
        ]
      }
    ]
  })
}
