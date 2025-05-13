#!/usr/bin/env python3
"""
Console store implementation for Diana.
"""

import logging
from typing import Dict

from diana.engine.stores.base import StoreManager

class ConsoleStore(StoreManager):
    """
    Store implementation that logs posterior updates to console.
    """
    
    def put(self, test_id: str, variant: str, alpha: int, beta: int, ts: str) -> None:
        """
        Log posterior update to console.
        
        Args:
            test_id: Experiment identifier
            variant: Variant name
            alpha: Alpha parameter of Beta distribution
            beta: Beta parameter of Beta distribution
            ts: Timestamp in ISO format
        """
        post_msg = {
            "test_id": test_id,
            "variant": variant,
            "timestamp": ts,
            "alpha": alpha,
            "beta": beta
        }
        logging.info("Posterior ► %s", post_msg)