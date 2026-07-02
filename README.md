# Beacon

A robot fleet telemetry and remote monitoring platform. ROS2 robots publish
telemetry over MQTT to AWS IoT Core, which fans the stream into a hot path for
live monitoring and a cold path for analytics. Built to run entirely in
simulation, with a no-hardware demo path.

> **Why this project exists.** Managing a fleet of robots is mostly a data
> problem: every robot is an unreliable network client emitting high-rate
> sensor data, and the operator needs a live view plus a queryable history
> without paying to store or move everything. Beacon is a working take on that
> pipeline using the current AWS robotics stack (IoT Core, Greengrass, Kinesis),
> not the deprecated RoboMaker path.

---

## How it works in plain terms

Picture a warehouse with a hundred robots. Each robot is like a phone that keeps
texting a short status update: where I am, my battery, am I healthy. Beacon is
the system that receives all those texts, keeps a live board of where everyone
is right now, files every update away for later, and taps someone on the
shoulder when a robot is in trouble.

The tricky ideas, in everyday language:

- **Hot path vs cold path.** Two copies of the data go to two different places
  for two different jobs. The hot path is the live dashboard: it only needs the
  latest status of each robot and it needs it instantly. The cold path is the
  filing cabinet: it keeps the full history so you can answer "how did battery
  drain look last Tuesday" later, cheaply. Trying to do both jobs with one store
  is what makes fleet systems slow and expensive.

- **Downsampling at the edge.** The robot's sensors chatter dozens of times a
  second, but the operator does not need that firehose. The robot summarizes and
  sends about once a second. Cutting the data down on the robot, before it hits
  the network, is the single biggest thing that keeps costs sane.

- **mTLS (per-robot certificates).** Every robot carries its own ID badge (an
  X.509 certificate). It can only clock in as itself and only speak on its own
  channel. If one robot is stolen or compromised, you cancel that one badge
  instead of changing the locks for the whole building.

- **Device shadow.** A sticky note in the cloud that holds each robot's desired
  settings (for example how often to report). If a robot was offline when you
  changed the setting, it reads the sticky note the moment it reconnects and
  catches up. You never have to assume every robot is online at once.

- **The offline sweep.** The live path only reacts when a message arrives, so a
  robot that crashes and goes silent would slip through unnoticed. A separate
  job runs a roll call every minute and flags anyone who has not checked in.
  Silence is treated as its own alarm.

---

## Follow one telemetry message

To make the whole pipeline concrete, here is the life of a single battery
reading from a robot named `robot-042`:

1. On the robot, sensors update constantly. The bridge keeps only the newest
   value from each and, once a second, packs them into one small JSON message
   (position, battery, health) tagged with the robot id and a timestamp.
2. It publishes that message over an encrypted MQTT connection to AWS IoT Core,
   on the topic `robots/robot-042/telemetry`. The robot's certificate proves who
   it is.
3. An IoT rule copies the message two ways at once: into Kinesis (live) and into
   Firehose (history).
4. The live copy triggers a Lambda. It writes robot-042's newest state to
   DynamoDB (overwriting the old "latest"), records a metric, and checks
   thresholds. Battery is 14 percent, under the limit, so it publishes an alert
   to SNS and you get an email.
5. The history copy is buffered by Firehose and written to S3 as compressed
   Parquet, filed under today's date, ready to be queried with Athena whenever.
6. Meanwhile the dashboard, reading only the "latest" row per robot, shows
   robot-042 turn red in about a second.

Same message, two destinations, two jobs. That split is the heart of the design.

---

## Architecture

```
   ROS2 robot (Gazebo or real)                          AWS
  ┌───────────────────────────┐
  │  /odom  /battery_state     │
  │  /cmd_vel  /diagnostics    │
  │            │               │            HOT PATH (live monitoring)
  │            ▼               │        ┌──────────────┐   ┌──────────────┐
  │   TelemetryCollector       │        │   Kinesis    │   │   Lambda     │
  │   (latest-value cache)     │        │  Data Stream │──▶│  processor   │
  │            │ 1 Hz          │        └──────────────┘   └──────┬───────┘
  │            ▼               │   MQTT        ▲                   │
  │      Beacon bridge  ───────┼──────────────┤          ┌────────┼─────────┐
  │      (mTLS, QoS1)          │  IoT Core     │          ▼        ▼         ▼
  │            ▲               │  topic rule ──┤     DynamoDB  CloudWatch  SNS
  │            │ shadow delta  │               │     (latest+   metrics   alerts
  └────────────┼───────────────┘               │      history)
               │                               │
        device shadow                          │   COLD PATH (analytics)
        (desired vs reported)                  │   ┌──────────┐  ┌─────────┐
                                               └──▶│ Firehose │─▶│ S3      │
                                                   │ (parquet)│  │ + Glue  │
                                                   └──────────┘  │ + Athena│
                                                                 └─────────┘

   Scheduled EventBridge (1 min) ──▶ Lambda alarm_evaluator ──▶ SNS
   (detects robots that went silent: absence of data is a signal)
```

### The AWS pieces, one line each

- **IoT Core**: the front door. Robots connect here over secure MQTT.
- **Kinesis Data Stream**: the live conveyor belt feeding the hot path.
- **Lambda (processor)**: reads the belt, updates the latest state, raises alerts.
- **DynamoDB**: the live board. One fast row per robot, plus short-lived history.
- **CloudWatch**: the graphs (battery, speed) over time.
- **SNS**: the notifier that emails or texts you on an alert.
- **Firehose**: the archivist that batches messages for storage.
- **S3 + Glue + Athena**: the filing cabinet (S3), its index (Glue), and the way
  you query it with SQL (Athena).
- **EventBridge**: the clock that triggers the every-minute roll call.

See `docs/architecture.md` for the message schema and the reasoning behind
each split.

---

## Repository layout

```
beacon/
  ros2_ws/src/beacon_bridge/     ROS2 package: the edge bridge
    beacon_bridge/
      telemetry_collector.py     subscribes to topics, caches latest values
      iot_client.py              AWS IoT SDK v2 wrapper (mTLS, shadow, QoS1)
      bridge.py                  the node: timer-driven publish + shadow config
    launch/bridge.launch.py
  cloud/
    lambda/telemetry_processor/  hot path: Kinesis -> DynamoDB + metrics + alerts
    lambda/alarm_evaluator/      scheduled offline sweep
    infra/                       Terraform for the whole cloud side
  dashboard/index.html          single-file live fleet view
  scripts/
    provision_thing.sh          register one robot as an IoT Thing + cert
    simulate_fleet.py           demo the pipeline with N virtual robots
  docker/                       ROS2 Humble container for the bridge
```

---

## Quickstart

You have two paths. The demo path needs no ROS2 install and no hardware.

### Path A: cloud demo in five minutes (no ROS2, no robot)

```bash
# 1. Deploy the cloud side
cd cloud/infra
terraform init
terraform apply -var="alert_email=you@example.com"   # confirm the SNS email

# 2. Get your IoT endpoint
aws iot describe-endpoint --endpoint-type iot:Data-ATS

# 3. Provision a few virtual robots (creates things + certs under ./certs)
cd ../../scripts
for i in 001 002 003; do ./provision_thing.sh robot-$i; done

# 4. Stream telemetry from virtual robots
pip install awsiotsdk
python simulate_fleet.py --endpoint <your-endpoint> --cert-dir ./certs --robots 3
```

Then open the DynamoDB table, watch CloudWatch metrics under `Beacon/Fleet`,
and let a battery drop below 20 percent to trigger an SNS email.

Want to see payloads with zero AWS setup first?

```bash
python scripts/simulate_fleet.py --dry-run --robots 3 --seconds 10
```

### Path B: real ROS2 robot in Gazebo

```bash
# Build the ROS2 package
cd ros2_ws
colcon build --symlink-install
source install/setup.bash

# Launch TurtleBot3 in Gazebo (any robot publishing /odom, /battery_state works)
export TURTLEBOT3_MODEL=burger
ros2 launch turtlebot3_gazebo turtlebot3_world.launch.py &

# Run the bridge (dry-run first to confirm it reads your topics)
ros2 run beacon_bridge bridge --ros-args -p device_id:=robot-001

# Then point it at IoT Core
ros2 run beacon_bridge bridge --ros-args \
  -p device_id:=robot-001 \
  -p iot_endpoint:=<your-endpoint> \
  -p cert_path:=./certs/robot-001.cert.pem \
  -p key_path:=./certs/robot-001.private.key \
  -p ca_path:=./certs/AmazonRootCA1.pem
```

### Dashboard

Open `dashboard/index.html` directly for demo data, or set `SOURCE_URL` to an
API Gateway endpoint backed by a DynamoDB query on `sk = "LATEST"`.

---

## Design decisions

**Downsample at the edge, not the cloud.** Sensors publish at tens of Hz; the
bridge caches the latest value per topic and uplinks at 1 Hz. Filtering before
the network is the largest single lever on IoT ingest cost and bandwidth, and
it is why the collector holds a latest-value cache instead of forwarding every
callback.

**mTLS per robot, least-privilege policy.** Each robot has its own X.509
certificate. The IoT policy uses the `iot:Connection.Thing.ThingName` variable
so a robot can only connect as itself and publish only to its own topic. One
compromised robot means one revoked cert, not a shared-secret rotation across
the fleet.

**At-least-once plus idempotent consumer.** Telemetry publishes at QoS 1.
Every message carries `device_id` and `ts`, and DynamoDB writes are keyed by
both, so a duplicate delivery overwrites identical data. No dedup table needed.

**Hot and cold paths are separate.** The Lambda hot path keeps only latest
state plus short-TTL history in DynamoDB for O(1) dashboard reads. The full
history goes through Firehose to S3 as date-partitioned Parquet, queryable with
Athena. Keeping analytics storage out of the hot Lambda keeps that function
fast and cheap.

**Absence of data is a signal.** The stream Lambda only fires when data
arrives, so a crashed robot would be invisible to it. A scheduled Lambda sweeps
the latest items every minute and alerts on any robot whose newest timestamp is
stale. That is the dead-man's-switch half of monitoring.

**Device shadow for config.** Desired state (for example the publish rate)
lives in the shadow. A robot that was offline reconciles on reconnect. This is
how you push config to robots without assuming they are all online.

---

## Extending it

- Run Greengrass V2 on the robot to aggregate and buffer telemetry during
  network outages, then batch-publish on reconnect.
- Add an Athena view plus QuickSight dashboard over the Parquet cold path.
- Add a `robots/+/commands` topic and a control API so operators can send
  return-to-dock commands through the shadow.

## License

MIT. See `LICENSE`.
