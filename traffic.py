#!/usr/bin/env python3
"""
Entry point for generating synthetic traffic.

This script provides backward compatibility with the original interface.
"""

from diana.cli.traffic_cli import main

if __name__ == "__main__":
    exit(main())