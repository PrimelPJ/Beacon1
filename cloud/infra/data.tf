resource "aws_kinesis_stream" "telemetry" {
  name             = "${var.project}-telemetry"
  shard_count      = var.kinesis_shards
  retention_period = 24
  stream_mode_details {
    stream_mode = "PROVISIONED"
  }
}

# Cold path: Firehose buffers messages and writes Parquet to S3, partitioned by
# date. Parquet plus partitioning is what makes Athena queries over history cheap.
resource "aws_kinesis_firehose_delivery_stream" "cold" {
  name        = "${var.project}-cold"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose.arn
    bucket_arn = aws_s3_bucket.cold.arn
    prefix     = "telemetry/dt=!{timestamp:yyyy-MM-dd}/"
    error_output_prefix = "errors/"
    buffering_size     = 64
    buffering_interval = 300
    compression_format = "UNCOMPRESSED" # parquet handled by the format conversion below

    data_format_conversion_configuration {
      enabled = true
      input_format_configuration {
        deserializer { open_x_json_ser_de {} }
      }
      output_format_configuration {
        serializer { parquet_ser_de {} }
      }
      schema_configuration {
        role_arn      = aws_iam_role.firehose.arn
        database_name = aws_glue_catalog_database.beacon.name
        table_name    = aws_glue_catalog_table.telemetry.name
        region        = var.region
      }
    }
  }
}

resource "aws_dynamodb_table" "telemetry" {
  name         = "${var.project}-telemetry"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "device_id"
  range_key    = "sk"

  attribute {
    name = "device_id"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

resource "aws_s3_bucket" "cold" {
  bucket        = "${var.project}-cold-${data.aws_caller_identity.me.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "cold" {
  bucket                  = aws_s3_bucket.cold.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cold" {
  bucket = aws_s3_bucket.cold.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_caller_identity" "me" {}

resource "aws_glue_catalog_database" "beacon" {
  name = "${replace(var.project, "-", "_")}_db"
}

# Minimal Glue table so Firehose can convert JSON to Parquet. Columns are the
# top level of the telemetry payload; nested maps are stored as struct/string.
resource "aws_glue_catalog_table" "telemetry" {
  name          = "telemetry"
  database_name = aws_glue_catalog_database.beacon.name
  table_type    = "EXTERNAL_TABLE"
  parameters    = { classification = "parquet" }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.cold.bucket}/telemetry/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    ser_de_info { serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe" }

    columns {
    name = "device_id"
    type = "string"
  }
    columns {
    name = "ts"
    type = "bigint"
  }
    columns {
    name = "pose"
    type = "string"
  }
    columns {
    name = "velocity"
    type = "string"
  }
    columns {
    name = "battery"
    type = "string"
  }
    columns {
    name = "health"
    type = "string"
  }
  }
}

resource "aws_iam_role" "firehose" {
  name = "${var.project}-firehose"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "firehose.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "firehose" {
  role = aws_iam_role.firehose.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow",
        Action = ["s3:AbortMultipartUpload", "s3:GetBucketLocation", "s3:GetObject",
                  "s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:PutObject"],
        Resource = [aws_s3_bucket.cold.arn, "${aws_s3_bucket.cold.arn}/*"] },
      { Effect = "Allow", Action = ["glue:GetTable", "glue:GetTableVersion", "glue:GetTableVersions"],
        Resource = "*" }
    ]
  })
}
