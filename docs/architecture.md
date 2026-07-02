# Beacon architecture notes

## Telemetry message schema

Published on `robots/<device_id>/telemetry`, one JSON object per message:

```json
{
  "device_id": "robot-001",
  "ts": 1719800000123,
  "pose":     { "x": 1.24, "y": -0.88, "yaw": 0.41 },
  "velocity": { "linear": 0.35, "angular": 0.0 },
  "command":  { "linear": 0.35, "angular": 0.0 },
  "battery":  { "percent": 82.0, "voltage": 11.32 },
  "health":   {
    "diagnostics": "ok",
    "ok": true,
    "faults": [],
    "odom_age_s": 0.02
  }
}
```

`ts` is epoch milliseconds so it sorts lexically as a DynamoDB sort key and is
trivially bucketed for time-series queries.

## DynamoDB single-table design

| PK (device_id) | SK          | meaning                                  |
|----------------|-------------|------------------------------------------|
| robot-001      | LATEST      | most recent state, overwritten each msg  |
| robot-001      | TS#<epoch>  | one item per sample, TTL-expired         |

- Dashboard read is `GetItem(device_id, "LATEST")`, O(1).
- History read is `Query(device_id, begins_with "TS#")` bounded by time.
- History items carry a `ttl` attribute so DynamoDB expires them automatically.
  Long-term history lives in S3, not here.

## Why partition on device_id in Kinesis

Using the device_id segment of the MQTT topic as the Kinesis partition key
keeps all of one robot's records on the same shard, preserving per-robot
ordering. Ordering across robots does not matter for this workload.

## Cost model (order of magnitude)

- 1,000 robots at 1 Hz is 1,000 msg/s. One Kinesis shard handles 1,000 records/s,
  so shard count scales roughly linearly with fleet size divided by 1,000.
- DynamoDB on-demand bills per write; the LATEST overwrite plus one history item
  is two writes per message.
- Firehose plus S3 Parquet is the cheap tier for the long tail of history.

## Failure handling

- Lambda event source mapping uses `bisect_batch_on_function_error` so one bad
  record cannot wedge a shard.
- MQTT uses `clean_session=false` so QoS 1 queues survive short disconnects.
- The scheduled sweep catches total silence (crash, power loss, radio dead).
