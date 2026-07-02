"""Launch the Beacon bridge with parameters read from the environment.

Set these before ros2 launch (see docker-compose for the containerized path):
  DEVICE_ID, IOT_ENDPOINT, CERT_PATH, KEY_PATH, CA_PATH, PUBLISH_HZ
"""
import os
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="beacon_bridge",
            executable="bridge",
            name="beacon_bridge",
            output="screen",
            parameters=[{
                "device_id": os.environ.get("DEVICE_ID", "robot-001"),
                "iot_endpoint": os.environ.get("IOT_ENDPOINT", ""),
                "cert_path": os.environ.get("CERT_PATH", ""),
                "key_path": os.environ.get("KEY_PATH", ""),
                "ca_path": os.environ.get("CA_PATH", ""),
                "publish_hz": float(os.environ.get("PUBLISH_HZ", "1.0")),
            }],
        ),
    ])
