# ROS2 Humble base with the Beacon bridge and the AWS IoT SDK installed.
FROM ros:humble-ros-base

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-pip ros-humble-nav-msgs ros-humble-sensor-msgs \
      ros-humble-geometry-msgs ros-humble-diagnostic-msgs \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir awsiotsdk

WORKDIR /ros2_ws
COPY ros2_ws/src ./src
RUN . /opt/ros/humble/setup.sh && colcon build --symlink-install

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "launch", "beacon_bridge", "bridge.launch.py"]
