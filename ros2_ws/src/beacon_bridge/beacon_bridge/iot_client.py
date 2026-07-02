"""Thin wrapper around the AWS IoT Device SDK v2 MQTT connection.

Handles the two things that trip people up in interviews:

1. mTLS auth. Each robot authenticates to IoT Core with its own X.509
   certificate and private key. There are no long-lived AWS credentials on
   the robot. If a robot is compromised you revoke one cert, not a shared key.

2. Named device shadows. The shadow is the source of truth for desired vs
   reported state. When a robot reconnects after a network drop it reads the
   desired shadow and reconciles, which is how you push config (for example a
   new telemetry rate or a "return to dock" flag) to a robot that was offline.

The publish uses QoS 1 (at least once). Telemetry is idempotent on the cloud
side because every message carries device_id plus ts, so a duplicate delivery
overwrites with identical data. That is the classic at-least-once plus
idempotent-consumer pattern.
"""

from __future__ import annotations

import json
import threading
from typing import Callable, Optional

from awscrt import mqtt
from awsiot import mqtt_connection_builder


class IoTClient:
    def __init__(
        self,
        endpoint: str,
        cert_path: str,
        key_path: str,
        ca_path: str,
        client_id: str,
        on_desired_state: Optional[Callable[[dict], None]] = None,
    ):
        self._client_id = client_id
        self._on_desired_state = on_desired_state
        self._connected = threading.Event()

        self._conn = mqtt_connection_builder.mtls_from_path(
            endpoint=endpoint,
            cert_filepath=cert_path,
            pri_key_filepath=key_path,
            ca_filepath=ca_path,
            client_id=client_id,
            clean_session=False,          # resume session so QoS1 queues survive drops
            keep_alive_secs=30,
            on_connection_interrupted=self._on_interrupted,
            on_connection_resumed=self._on_resumed,
        )

    # ---- lifecycle ----------------------------------------------------------

    def connect(self) -> None:
        self._conn.connect().result()
        self._connected.set()
        # Subscribe to the desired-state delta so config pushes reach the robot.
        shadow_delta = f"$aws/things/{self._client_id}/shadow/update/delta"
        self._conn.subscribe(
            topic=shadow_delta,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=self._on_shadow_delta,
        )[0].result()

    def disconnect(self) -> None:
        self._conn.disconnect().result()
        self._connected.clear()

    # ---- publish ------------------------------------------------------------

    def publish_telemetry(self, topic: str, payload: dict) -> None:
        self._conn.publish(
            topic=topic,
            payload=json.dumps(payload),
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )

    def report_shadow(self, reported: dict) -> None:
        """Publish reported state so the cloud can compare against desired."""
        topic = f"$aws/things/{self._client_id}/shadow/update"
        self._conn.publish(
            topic=topic,
            payload=json.dumps({"state": {"reported": reported}}),
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )

    # ---- callbacks ----------------------------------------------------------

    def _on_shadow_delta(self, topic: str, payload: bytes, **kwargs) -> None:
        try:
            doc = json.loads(payload)
            desired = doc.get("state", {})
        except (ValueError, AttributeError):
            return
        if desired and self._on_desired_state:
            self._on_desired_state(desired)

    def _on_interrupted(self, connection, error, **kwargs) -> None:
        self._connected.clear()

    def _on_resumed(self, connection, return_code, session_present, **kwargs) -> None:
        self._connected.set()
