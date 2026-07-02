"""Beacon telemetry bridge node.

Timeline of one robot:
  1. Subscribe to ROS2 topics (via TelemetryCollector).
  2. Connect to AWS IoT Core over mTLS.
  3. On a timer (default 1 Hz), snapshot the latest state and publish it to
     the ingest topic  robots/<device_id>/telemetry.
  4. Accept desired-state deltas from the device shadow to change the publish
     rate at runtime, without redeploying.

Run:
  ros2 run beacon_bridge bridge --ros-args \
    -p device_id:=robot-001 \
    -p iot_endpoint:=xxxx-ats.iot.us-west-2.amazonaws.com \
    -p cert_path:=/certs/robot-001.cert.pem \
    -p key_path:=/certs/robot-001.private.key \
    -p ca_path:=/certs/AmazonRootCA1.pem
"""

from __future__ import annotations

import rclpy
from rclpy.node import Node

from beacon_bridge.telemetry_collector import TelemetryCollector
from beacon_bridge.iot_client import IoTClient


class BeaconBridge(Node):
    def __init__(self):
        super().__init__("beacon_bridge")

        self.declare_parameter("device_id", "robot-001")
        self.declare_parameter("iot_endpoint", "")
        self.declare_parameter("cert_path", "")
        self.declare_parameter("key_path", "")
        self.declare_parameter("ca_path", "")
        self.declare_parameter("publish_hz", 1.0)

        self._device_id = self.get_parameter("device_id").value
        endpoint = self.get_parameter("iot_endpoint").value
        cert = self.get_parameter("cert_path").value
        key = self.get_parameter("key_path").value
        ca = self.get_parameter("ca_path").value
        self._hz = float(self.get_parameter("publish_hz").value)

        self._collector = TelemetryCollector(self)

        if endpoint:
            self._iot = IoTClient(
                endpoint=endpoint,
                cert_path=cert,
                key_path=key,
                ca_path=ca,
                client_id=self._device_id,
                on_desired_state=self._apply_desired_state,
            )
            self._iot.connect()
            self._iot.report_shadow({"publish_hz": self._hz, "firmware": "beacon-1.0"})
            self.get_logger().info(f"connected to IoT Core as {self._device_id}")
        else:
            # No endpoint means dry-run: log payloads instead of publishing.
            self._iot = None
            self.get_logger().warn("no iot_endpoint set, running in dry-run (log only) mode")

        self._topic = f"robots/{self._device_id}/telemetry"
        self._timer = self.create_timer(1.0 / self._hz, self._on_timer)

    def _on_timer(self) -> None:
        payload = self._collector.snapshot(self._device_id)
        if self._iot:
            self._iot.publish_telemetry(self._topic, payload)
        else:
            self.get_logger().info(str(payload))

    def _apply_desired_state(self, desired: dict) -> None:
        """Handle a shadow delta. Only publish_hz is supported here as an example."""
        if "publish_hz" in desired:
            new_hz = float(desired["publish_hz"])
            if new_hz > 0 and new_hz != self._hz:
                self._hz = new_hz
                self._timer.cancel()
                self._timer = self.create_timer(1.0 / self._hz, self._on_timer)
                self.get_logger().info(f"publish rate changed to {self._hz} Hz via shadow")
                if self._iot:
                    self._iot.report_shadow({"publish_hz": self._hz})


def main(args=None):
    rclpy.init(args=args)
    node = BeaconBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
