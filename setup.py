#!/usr/bin/env python3
"""
Setup script for Diana.
"""

from setuptools import setup, find_packages

setup(
    name="diana",
    version="0.1.0",
    description="Bayesian A/B Testing Engine",
    author="Diana Team",
    packages=find_packages(),
    install_requires=[
        "confluent-kafka>=2.0.0",
        "boto3>=1.28.0",
        "pyyaml>=6.0",
        "pandas>=1.5.0",
        "scipy>=1.10.0",
    ],
    entry_points={
        "console_scripts": [
            "diana-engine=diana.cli.engine_cli:main",
            "diana-analyze=diana.cli.analyze_cli:main",
            "diana-traffic=diana.cli.traffic_cli:main",
            "diana-monitor=diana.cli.monitor_cli:main",
        ],
    },
    python_requires=">=3.9",
)