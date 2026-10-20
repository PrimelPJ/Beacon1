"""Collects data from multiple ROS2 topics and assembles a single telemetry payload.

The collector subscribes to the standard nav/sensor topics a mobile robot
publishes and keeps the latest value from each. On a fixed timer the bridge
node calls snapshot() to get one flat dict that becomes one MQTT message.

Keeping the latest-value cache here (rather than publishing on every callback)
decouples the sensor rates from the uplink rate. A robot might publish odometry
at 50 Hz, but we only want to send telemetry to the cloud at 1 Hz. That
downsampling at the edge is the single biggest lever on IoT ingest cost.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from nav_msgs.msg import Odometry
from sensor_msgs.msg import BatteryState
from geometry_msgs.msg import Twist
from diagnostic_msgs.msg import DiagnosticArray

# Number of seconds without an odometry update before the robot is flagged
# as stale in the telemetry health payload.
STALE_ODOM_THRESHOLD_S: float = 5.0


@dataclass
class TelemetryState:
    """Latest-known values. None means we have not heard from that topic yet."""

    x: Optional[float] = None
    y: Optional[float] = None
    yaw: Optional[float] = None
    linear_speed: Optional[float] = None
    angular_speed: Optional[float] = None
    battery_percent: Optional[float] = None
    battery_voltage: Optional[float] = None
    cmd_linear: Optional[float] = None
    cmd_angular: Optional[float] = None
    diagnostics_ok: bool = True
    diagnostics_summary: str = "unknown"
    last_odom_ts: float = 0.0
    faults: list = field(default_factory=list)


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """Extract yaw (rotation about z) from a quaternion. Standard ROS convention."""
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


class TelemetryCollector:
    """Wires up subscriptions on an existing rclpy Node and caches the latest values."""

    def __init__(self, node: Node):
        self._node = node
        self._state = TelemetryState()

        # Sensor data QoS: best-effort, keep last. High-rate sensor streams should
        # not force the middleware to guarantee delivery of stale samples.
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        node.create_subscription(Odometry, "/odom", self._on_odom, sensor_qos)
        node.create_subscription(BatteryState, "/battery_state", self._on_battery, sensor_qos)
        node.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 10)
        node.create_subscription(DiagnosticArray, "/diagnostics", self._on_diagnostics, 10)

    # ---- callbacks ----------------------------------------------------------

    def _on_odom(self, msg: Odometry) -> None:
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        v = msg.twist.twist
        self._state.x = round(p.x, 4)
        self._state.y = round(p.y, 4)
        self._state.yaw = round(yaw_from_quaternion(q.x, q.y, q.z, q.w), 4)
        self._state.linear_speed = round(math.hypot(v.linear.x, v.linear.y), 4)
        self._state.angular_speed = round(v.angular.z, 4)
        self._state.last_odom_ts = time.time()

    def _on_battery(self, msg: BatteryState) -> None:
        # percentage is 0.0 to 1.0 in the ROS message; publish as 0 to 100.
        if not math.isnan(msg.percentage):
            self._state.battery_percent = round(msg.percentage * 100.0, 1)
        if not math.isnan(msg.voltage):
            self._state.battery_voltage = round(msg.voltage, 2)

    def _on_cmd_vel(self, msg: Twist) -> None:
        self._state.cmd_linear = round(msg.linear.x, 4)
        self._state.cmd_angular = round(msg.angular.z, 4)

    def _on_diagnostics(self, msg: DiagnosticArray) -> None:
        faults = []
        worst = 0  # 0 OK, 1 WARN, 2 ERROR, 3 STALE
        for status in msg.status:
            if status.level > 0:
                faults.append({"name": status.name, "level": int(status.level),
                               "message": status.message})
            worst = max(worst, int(status.level))
        self._state.faults = faults
        self._state.diagnostics_ok = worst == 0
        self._state.diagnostics_summary = {0: "ok", 1: "warn", 2: "error", 3: "stale"}.get(worst, "unknown")

    # ---- snapshot -----------------------------------------------------------

    def snapshot(self, device_id: str) -> dict:
        """Build one telemetry message. Called by the bridge on its publish timer."""
        s = self._state
        odom_age = time.time() - s.last_odom_ts if s.last_odom_ts else None
        return {
            "device_id": device_id,
            "ts": int(time.time() * 1000),  # epoch millis, sortable in DynamoDB
            "pose": {"x": s.x, "y": s.y, "yaw": s.yaw},
            "velocity": {"linear": s.linear_speed, "angular": s.angular_speed},
            "command": {"linear": s.cmd_linear, "angular": s.cmd_angular},
            "battery": {"percent": s.battery_percent, "voltage": s.battery_voltage},
            "health": {
                "diagnostics": s.diagnostics_summary,
                "ok": s.diagnostics_ok,
                "faults": s.faults,
                "odom_age_s": round(odom_age, 2) if odom_age is not None else None,
                "odom_stale": odom_age is not None and odom_age > STALE_ODOM_THRESHOLD_S,
            },
        }
