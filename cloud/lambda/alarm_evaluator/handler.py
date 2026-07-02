"""Scheduled Lambda that flags robots that have gone silent.

The hot-path Lambda only fires when data arrives. A robot that drops off the
network entirely produces no records, so nothing there would notice. This
function runs on an EventBridge schedule (default every minute), scans the
LATEST items, and raises an alert for any device whose newest ts is older than
OFFLINE_THRESHOLD_S.

This is the "dead man's switch" half of monitoring: absence of data is itself
a signal. A common interview follow-up is "how do you detect a robot that
crashed" and the answer is a scheduled sweep like this, not the stream.
"""

from __future__ import annotations

import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Attr

TABLE_NAME = os.environ.get("TABLE_NAME", "beacon-telemetry")
ALERT_TOPIC_ARN = os.environ.get("ALERT_TOPIC_ARN", "")
OFFLINE_THRESHOLD_S = float(os.environ.get("OFFLINE_THRESHOLD_S", "30"))

_table = boto3.resource("dynamodb").Table(TABLE_NAME)
_sns = boto3.client("sns") if ALERT_TOPIC_ARN else None


def handler(event, context):
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - int(OFFLINE_THRESHOLD_S * 1000)

    # Scan only LATEST items. For large fleets use a GSI on sk instead of a scan.
    resp = _table.scan(FilterExpression=Attr("sk").eq("LATEST"))
    offline = []
    for item in resp.get("Items", []):
        ts = int(item.get("ts", 0))
        if ts < cutoff:
            offline.append({"device_id": item["device_id"],
                            "last_seen_s_ago": round((now_ms - ts) / 1000, 1)})

    if offline and _sns:
        _sns.publish(
            TopicArn=ALERT_TOPIC_ARN,
            Subject=f"[Beacon] {len(offline)} robot(s) offline",
            Message=json.dumps(offline, indent=2),
        )
    return {"offline_count": len(offline), "offline": offline}
