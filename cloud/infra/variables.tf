variable "region" {
  type    = string
  default = "us-west-2"
}

variable "project" {
  type    = string
  default = "beacon"
}

variable "alert_email" {
  type        = string
  description = "Email subscribed to the SNS alert topic. Leave blank to skip."
  default     = ""
}

variable "kinesis_shards" {
  type    = number
  default = 1
}
