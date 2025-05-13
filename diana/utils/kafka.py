#!/usr/bin/env python3
"""
Kafka utilities for Diana.
"""

import json
import logging
import time
from typing import List, Dict, Any, Optional

try:
    import ujson as fast_json
    has_ujson = True
except ImportError:
    has_ujson = False
    
from confluent_kafka import Producer, Consumer, KafkaError, TopicPartition, Message

def to_json(obj) -> bytes:
    """
    Serialize object to JSON bytes, using ujson if available for better performance.
    
    Args:
        obj: Python object to serialize
        
    Returns:
        bytes: JSON-encoded data as bytes
    """
    try:
        if has_ujson:
            return fast_json.dumps(obj).encode()
        else:
            return json.dumps(obj).encode()
    except Exception:
        # Fallback to standard json if ujson fails
        return json.dumps(obj).encode()

class KafkaManager:
    """
    Manages Kafka producers and consumers with retry logic.
    """
    
    def __init__(self, broker_config: Dict[str, str], topics: Dict[str, str], 
                 consumer_group: str = "diana_engine"):
        """
        Initialize Kafka connections.
        
        Args:
            broker_config: Kafka broker configuration
            topics: Dictionary mapping topic types to names
            consumer_group: Consumer group ID
        """
        self.broker_config = broker_config
        self.topics = topics
        self.consumer_group = consumer_group
        
        # Initialize producer
        self.producer = self._create_producer()
        
        # Initialize consumer
        self.consumer = self._create_consumer()
        if "raw_events_in" in self.topics:
            self.consumer.subscribe([self.topics["raw_events_in"]])
    
    def _create_producer(self) -> Producer:
        """Create Kafka producer with retry logic."""
        retry_count = 0
        max_retries = 5
        
        while retry_count < max_retries:
            try:
                return Producer(self.broker_config)
            except Exception as e:
                retry_count += 1
                wait_time = min(30, 2 ** retry_count)
                logging.warning(f"Failed to create Kafka producer (attempt {retry_count}): {e}")
                if retry_count >= max_retries:
                    raise
                time.sleep(wait_time)
    
    def _create_consumer(self) -> Consumer:
        """Create Kafka consumer with retry logic."""
        retry_count = 0
        max_retries = 5
        
        consumer_config = {
            **self.broker_config,
            "group.id": self.consumer_group,
            "auto.offset.reset": "earliest",
        }
        
        while retry_count < max_retries:
            try:
                return Consumer(consumer_config)
            except Exception as e:
                retry_count += 1
                wait_time = min(30, 2 ** retry_count)
                logging.warning(f"Failed to create Kafka consumer (attempt {retry_count}): {e}")
                if retry_count >= max_retries:
                    raise
                time.sleep(wait_time)
    
    def publish(self, topic_type: str, message: Dict[str, Any]) -> None:
        """
        Publish message to specified topic type.
        
        Args:
            topic_type: Topic type (e.g., "metrics_out", "posteriors_out")
            message: Message payload to publish
        """
        if topic_type not in self.topics:
            raise ValueError(f"Unknown topic type: {topic_type}")
            
        topic = self.topics[topic_type]
        self.producer.produce(topic, to_json(message))
    
    def poll_messages(self, timeout: float = 1.0) -> Optional[Message]:
        """
        Poll for a single message.
        
        Args:
            timeout: Poll timeout in seconds
            
        Returns:
            Message or None if no message available
        """
        msg = self.consumer.poll(timeout)
        
        if msg is None:
            return None
            
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                logging.error(f"Kafka error: {msg.error()}")
            return None
            
        return msg
    
    def commit(self, asynchronous: bool = True) -> None:
        """
        Commit consumer offsets.
        
        Args:
            asynchronous: Whether to commit asynchronously
        """
        self.consumer.commit(asynchronous=asynchronous)
    
    def flush(self, timeout: int = 5) -> None:
        """
        Flush producer to ensure all messages are delivered.
        
        Args:
            timeout: Flush timeout in seconds
        """
        self.producer.flush(timeout)