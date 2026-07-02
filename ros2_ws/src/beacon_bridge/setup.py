from setuptools import find_packages, setup
import os
from glob import glob

package_name = "beacon_bridge"

setup(
    name=package_name,
    version="1.0.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages",
            ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools", "awsiotsdk"],
    zip_safe=True,
    maintainer="Primel Jayawardana",
    maintainer_email="you@primelj.dev",
    description="ROS2 to AWS IoT Core telemetry bridge",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "bridge = beacon_bridge.bridge:main",
        ],
    },
)
