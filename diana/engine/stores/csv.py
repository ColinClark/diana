#!/usr/bin/env python3
"""
CSV store implementation for Diana.
"""

import csv
from pathlib import Path

from diana.engine.stores.base import StoreManager

class CSVStore(StoreManager):
    """
    Store implementation that writes posterior updates to CSV.
    """
    
    def __init__(self, file_path: str):
        """
        Initialize CSV store.
        
        Args:
            file_path: Path to CSV file
        """
        self.file_path = Path(file_path)
        self._ensure_file_exists()
    
    def _ensure_file_exists(self) -> None:
        """
        Create CSV file with header if it doesn't exist.
        """
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.file_path.open('w', newline='') as f:
                csv.writer(f).writerow(
                    ['timestamp', 'test_id', 'variant', 'alpha', 'beta'])
    
    def put(self, test_id: str, variant: str, alpha: int, beta: int, ts: str) -> None:
        """
        Write posterior update to CSV.
        
        Args:
            test_id: Experiment identifier
            variant: Variant name
            alpha: Alpha parameter of Beta distribution
            beta: Beta parameter of Beta distribution
            ts: Timestamp in ISO format
        """
        with self.file_path.open('a', newline='') as f:
            csv.writer(f).writerow([ts, test_id, variant, alpha, beta])