"""Simulate a fleet of robots publishing telemetry to AWS IoT Core.

This exists so you can demo the whole cloud pipeline in an interview without
Gazebo or real robots. Each virtual robot drives a random walk, drains its
battery, and occasionally raises a fault, then publishes on the same topic
(robots/<device_id>/telemetry) the real ROS2 bridge uses. The cloud side cannot
tell the difference.

Usage:
  # Real: publish to IoT Core (needs one cert set per device, see scripts/provision_thing.sh)
  python simulate_fleet.py --endpoint xxxx-ats.iot.us-west-2.amazonaws.com \
      --cert-dir ./certs --robots 5

  # Dry run: no AWS, just print payloads to verify the shape.
  python simulate_fleet.py --dry-run --robots 3 --seconds 10
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time

try:
    from awscrt import mqtt
    from awsiot import mqtt_connection_builder
    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False


class VirtualRobot:
    def __init__(self, device_id: str, seed: int = 0):
        self.device_id = device_id
        self.rng = random.Random(seed)
        self.x = self.rng.uniform(-5, 5)
        self.y = self.rng.uniform(-5, 5)
        self.yaw = self.rng.uniform(-math.pi, math.pi)
        self.battery = self.rng.uniform(70, 100)
        self.fault_active = False

    def step(self, dt: float) -> dict:
        # random-walk heading, constant-ish speed
        self.yaw += self.rng.uniform(-0.3, 0.3)
        speed = max(0.0, self.rng.gauss(0.35, 0.1))
        self.x += math.cos(self.yaw) * speed * dt
        self.y += math.sin(self.yaw) * speed * dt
        self.battery = max(0.0, self.battery - self.rng.uniform(0.05, 0.2))

        # 1% chance per step to raise or clear a fault
        if self.rng.random() < 0.01:
            self.fault_active = not self.fault_active

        faults = []
        if self.fault_active:
            faults = [{"name": "motor_left", "level": 2, "message": "over-temperature"}]

        return {
            "device_id": self.device_id,
            "ts": int(time.time() * 1000),
            "pose": {"x": round(self.x, 4), "y": round(self.y, 4), "yaw": round(self.yaw, 4)},
            "velocity": {"linear": round(speed, 4), "angular": 0.0},
            "command": {"linear": round(speed, 4), "angular": 0.0},
            "battery": {"percent": round(self.battery, 1), "voltage": round(10.5 + self.battery / 100, 2)},
            "health": {
                "diagnostics": "error" if faults else "ok",
                "ok": not faults,
                "faults": faults,
                "odom_age_s": 0.0,
            },
        }


def connect(endpoint: str, cert_dir: str, device_id: str):
    return mqtt_connection_builder.mtls_from_path(
        endpoint=endpoint,
        cert_filepath=os.path.join(cert_dir, f"{device_id}.cert.pem"),
        pri_key_filepath=os.path.join(cert_dir, f"{device_id}.private.key"),
        ca_filepath=os.path.join(cert_dir, "AmazonRootCA1.pem"),
        client_id=device_id,
        clean_session=False,
        keep_alive_secs=30,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="")
    ap.add_argument("--cert-dir", default="./certs")
    ap.add_argument("--robots", type=int, default=3)
    ap.add_argument("--hz", type=float, default=1.0)
    ap.add_argument("--seconds", type=float, default=0, help="0 means run forever")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    robots = [VirtualRobot(f"robot-{i:03d}", seed=i) for i in range(1, args.robots + 1)]
    conns = {}
    if not args.dry_run:
        if not HAVE_SDK:
            raise SystemExit("awsiotsdk not installed. pip install awsiotsdk, or use --dry-run")
        for r in robots:
            c = connect(args.endpoint, args.cert_dir, r.device_id)
            c.connect().result()
            conns[r.device_id] = c
        print(f"connected {len(conns)} robots to {args.endpoint}")

    dt = 1.0 / args.hz
    start = time.time()
    try:
        while True:
            for r in robots:
                payload = r.step(dt)
                topic = f"robots/{r.device_id}/telemetry"
                if args.dry_run:
                    print(topic, json.dumps(payload))
                else:
                    conns[r.device_id].publish(topic, json.dumps(payload), mqtt.QoS.AT_LEAST_ONCE)
            if args.seconds and (time.time() - start) >= args.seconds:
                break
            time.sleep(dt)
    except KeyboardInterrupt:
        pass
    finally:
        for c in conns.values():
            c.disconnect().result()
    print("done")


if __name__ == "__main__":
    main()
