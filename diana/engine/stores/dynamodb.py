#!/usr/bin/env python3
"""
DynamoDB store implementation for Diana.
"""

import logging
import boto3
from botocore.exceptions import ClientError

from diana.engine.stores.base import StoreManager

class DynamoDBStore(StoreManager):
    """
    Store implementation that writes posterior updates to DynamoDB.
    """
    
    def __init__(self, table_name: str, region: str):
        """
        Initialize DynamoDB store.
        
        Args:
            table_name: DynamoDB table name
            region: AWS region
        """
        self.table_name = table_name
        self.region = region
        self._init_table()
    
    def _init_table(self) -> None:
        """
        Initialize DynamoDB table connection.
        """
        try:
            self.table = boto3.resource("dynamodb", region_name=self.region).Table(self.table_name)
        except Exception as e:
            logging.error(f"Failed to initialize DynamoDB connection: {e}")
            raise
    
    def put(self, test_id: str, variant: str, alpha: int, beta: int, ts: str) -> None:
        """
        Write posterior update to DynamoDB.
        
        Args:
            test_id: Experiment identifier
            variant: Variant name
            alpha: Alpha parameter of Beta distribution
            beta: Beta parameter of Beta distribution
            ts: Timestamp in ISO format
        """
        try:
            self.table.put_item(Item=dict(
                test_id=test_id, 
                variant=variant,
                timestamp=ts, 
                alpha=alpha, 
                beta=beta
            ))
        except ClientError as e:
            logging.error(f"Failed to write to DynamoDB: {e}")
            raise