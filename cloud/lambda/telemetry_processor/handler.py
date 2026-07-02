"""Kinesis consumer Lambda for the Beacon hot path.

Triggered by the Kinesis Data Stream that AWS IoT Core rules forward telemetry
into. For each record it:

  1. Upserts the latest state for the device into DynamoDB (single item per
     device, keyed by device_id) so the dashboard reads O(1).
  2. Appends a time-series item (device_id + ts) for history and replay.
  3. Emits CloudWatch custom metrics (battery, speed) per device.
  4. Publishes an SNS alert if a threshold is breached (low battery, fault,
     stale odometry).

Idempotency: writes are keyed by device_id and ts, so a duplicate Kinesis
delivery (at-least-once) rewrites identical data. No dedup table needed.

The cold path (full history as Parquet in S3) is handled separately by Kinesis
Firehose, not here. Keeping analytics storage out of the hot Lambda keeps this
function fast and cheap. See cloud/infra/kinesis.tf.
"""

from __future__ import annotations

import base64
import json
import os
from decimal import Decimal

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "beacon-telemetry")
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "Beacon/Fleet")
ALERT_TOPIC_ARN = os.environ.get("ALERT_TOPIC_ARN", "")
LOW_BATTERY_PCT = float(os.environ.get("LOW_BATTERY_PCT", "20"))
STALE_ODOM_S = float(os.environ.get("STALE_ODOM_S", "5"))
HISTORY_TTL_DAYS = int(os.environ.get("HISTORY_TTL_DAYS", "14"))

_dynamodb = boto3.resource("dynamodb")
_table = _dynamodb.Table(TABLE_NAME)
_cloudwatch = boto3.client("cloudwatch")
_sns = boto3.client("sns") if ALERT_TOPIC_ARN else None


def _to_decimal(obj):
    """DynamoDB rejects floats. Convert recursively via the JSON round-trip."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def _evaluate_alerts(t: dict) -> list:
    alerts = []
    battery = (t.get("battery") or {}).get("percent")
    if battery is not None and battery <= LOW_BATTERY_PCT:
        alerts.append(f"battery low: {battery}%")

    health = t.get("health") or {}
    if not health.get("ok", True):
        alerts.append(f"diagnostics {health.get('diagnostics')}: {health.get('faults')}")

    odom_age = health.get("odom_age_s")
    if odom_age is not None and odom_age > STALE_ODOM_S:
        alerts.append(f"odometry stale for {odom_age}s")
    return alerts


def _emit_metrics(t: dict) -> None:
    device_id = t["device_id"]
    data = []
    battery = (t.get("battery") or {}).get("percent")
    speed = (t.get("velocity") or {}).get("linear")
    if battery is not None:
        data.append({"MetricName": "BatteryPercent", "Value": battery, "Unit": "Percent",
                     "Dimensions": [{"Name": "DeviceId", "Value": device_id}]})
    if speed is not None:
        data.append({"MetricName": "LinearSpeed", "Value": speed, "Unit": "None",
                     "Dimensions": [{"Name": "DeviceId", "Value": device_id}]})
    if data:
        _cloudwatch.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=data)


def _persist(t: dict) -> None:
    device_id = t["device_id"]
    ts = t["ts"]
    item = _to_decimal(t)

    # Latest-state item: SK "LATEST", overwritten every message.
    latest = dict(item)
    latest["sk"] = "LATEST"
    _table.put_item(Item=latest)

    # Time-series item: SK is the timestamp, with a TTL so history self-expires.
    history = dict(item)
    history["sk"] = f"TS#{ts}"
    history["ttl"] = (ts // 1000) + HISTORY_TTL_DAYS * 86400
    _table.put_item(Item=history)


def handler(event, context):
    processed = 0
    alerted = 0
    for record in event.get("Records", []):
        raw = base64.b64decode(record["kinesis"]["data"])
        try:
            t = json.loads(raw)
        except ValueError:
            continue
        if "device_id" not in t or "ts" not in t:
            continue

        _persist(t)
        _emit_metrics(t)

        alerts = _evaluate_alerts(t)
        if alerts and _sns:
            _sns.publish(
                TopicArn=ALERT_TOPIC_ARN,
                Subject=f"[Beacon] {t['device_id']} alert",
                Message=json.dumps({"device_id": t["device_id"], "ts": t["ts"],
                                    "alerts": alerts}, indent=2),
            )
            alerted += 1
        processed += 1

    return {"processed": processed, "alerted": alerted}
