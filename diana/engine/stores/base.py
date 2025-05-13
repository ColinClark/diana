#!/usr/bin/env python3
"""
Base storage interface for Diana.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any

class StoreManager(ABC):
    """
    Abstract base class for posterior storage.
    """
    
    @abstractmethod
    def put(self, test_id: str, variant: str, alpha: int, beta: int, ts: str) -> None:
        """
        Store a posterior update.
        
        Args:
            test_id: Experiment identifier
            variant: Variant name
            alpha: Alpha parameter of Beta distribution
            beta: Beta parameter of Beta distribution
            ts: Timestamp in ISO format
        """
        pass
    
    @classmethod
    def create(cls, sink_type: str, config: Dict[str, Any]) -> 'StoreManager':
        """
        Factory method to create appropriate store based on sink_type.
        
        Args:
            sink_type: Type of sink ("console", "csv", "dynamodb")
            config: Configuration dictionary
            
        Returns:
            StoreManager: Appropriate storage manager implementation
            
        Raises:
            ValueError: If sink_type is not supported
        """
        if sink_type == "console":
            from diana.engine.stores.console import ConsoleStore
            return ConsoleStore()
        elif sink_type == "csv":
            from diana.engine.stores.csv import CSVStore
            return CSVStore(config.get("csv_output_path", "posteriors.csv"))
        elif sink_type == "dynamodb":
            from diana.engine.stores.dynamodb import DynamoDBStore
            dynamo_config = config.get("infrastructure", {}).get("dynamodb", {})
            return DynamoDBStore(
                table_name=dynamo_config.get("tables", {}).get("posteriors", "ab_posteriors"),
                region=dynamo_config.get("region", "us-east-1")
            )
        else:
            raise ValueError(f"Unknown sink type: {sink_type}")