#!/usr/bin/env python3
"""
Entry point for the Bayesian engine.

This script provides backward compatibility with the original interface.
"""

from diana.cli.engine_cli import main

if __name__ == "__main__":
    exit(main())