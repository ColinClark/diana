#!/usr/bin/env python3
"""
Configuration utilities for Diana.
"""

import os
import yaml
from pathlib import Path
from datetime import datetime, timezone

def iso_now() -> str:
    """
    Get current UTC time in ISO 8601 format.
    
    Returns:
        str: Current time in ISO format with seconds precision
    """
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")

def load_config(path: Path) -> dict:
    """
    Load configuration from YAML file.
    
    Args:
        path (Path): Path to YAML configuration file
        
    Returns:
        dict: Parsed configuration
    """
    with path.open() as fh:
        config = yaml.safe_load(fh)
    
    # Override with environment variables when available
    if "infrastructure" in config and "dynamodb" in config["infrastructure"]:
        if "AWS_REGION" in os.environ:
            config["infrastructure"]["dynamodb"]["region"] = os.environ["AWS_REGION"]
    
    return config