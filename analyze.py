#!/usr/bin/env python3
"""
Entry point for analyzing posteriors.

This script provides backward compatibility with the original interface.
"""

from diana.cli.analyze_cli import main

if __name__ == "__main__":
    exit(main())