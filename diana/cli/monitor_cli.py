#!/usr/bin/env python3
"""
Command-line interface for monitoring Kafka topics.

This module provides real-time monitoring of all Kafka topics
that the Diana engine writes to during processing.
"""

import argparse
import json
import logging
import signal
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

from confluent_kafka import Consumer, KafkaError

from diana.utils.config import load_config


class KafkaMonitor:
    """
    Real-time monitor for Kafka topics written by the Diana engine.
    """
    
    def __init__(self, broker_config: Dict[str, str], topics: list, consumer_group: str = "diana_monitor"):
        """
        Initialize the Kafka monitor.
        
        Args:
            broker_config: Kafka broker configuration
            topics: List of topics to monitor
            consumer_group: Consumer group ID
        """
        self.topics = topics
        self.running = True
        
        # Create consumer
        consumer_config = {
            **broker_config,
            "group.id": consumer_group,
            "auto.offset.reset": "latest",  # Start from latest messages
            "enable.auto.commit": True,
        }
        
        self.consumer = Consumer(consumer_config)
        self.consumer.subscribe(topics)
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logging.info("Received shutdown signal, stopping monitor...")
        self.running = False
    
    def _format_message(self, msg) -> str:
        """
        Format a Kafka message for display.
        
        Args:
            msg: Kafka message
            
        Returns:
            Formatted message string
        """
        try:
            # Parse message value as JSON
            value = json.loads(msg.value().decode('utf-8'))
            
            # Get timestamp
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            
            # Format based on message type
            topic = msg.topic()
            
            if topic.endswith('posteriors') or 'posterior' in topic:
                # Posterior snapshot message
                test_id = value.get('test_id', 'unknown')
                return (f"[{timestamp}] [POSTERIOR] [{test_id}] | {topic} | "
                       f"variant={value.get('variant', 'unknown')} "
                       f"α={value.get('alpha', 0)} β={value.get('beta', 0)}")
            
            elif topic.endswith('metrics') or 'metric' in topic:
                # Metrics message
                if 'events_processed' in value:
                    # Health metrics
                    return (f"[{timestamp}] [HEALTH] | {topic} | "
                           f"processed={value.get('events_processed', 0)} "
                           f"skipped={value.get('events_skipped', 0)} "
                           f"errors={value.get('error_count', 0)}")
                else:
                    # Variant metrics
                    test_id = value.get('test_id', 'unknown')
                    return (f"[{timestamp}] [METRICS] [{test_id}] | {topic} | "
                           f"variant={value.get('variant', 'unknown')} "
                           f"exp_inc={value.get('exposures_inc', 0)} "
                           f"suc_inc={value.get('successes_inc', 0)} "
                           f"rate={value.get('success_rate_total', 0):.3f}")
            
            elif topic.endswith('decisions') or 'decision' in topic:
                # Decision message
                test_id = value.get('test_id', 'unknown')
                return (f"[{timestamp}] [DECISION] [{test_id}] | {topic} | "
                       f"variant={value.get('variant', 'unknown')} "
                       f"confidence={value.get('confidence', 0):.3f}")
            
            else:
                # Generic message
                return (f"[{timestamp}] [MESSAGE] | {topic} | "
                       f"{json.dumps(value, indent=None, separators=(',', ':'))}")
        
        except Exception as e:
            # Fallback for non-JSON messages
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            return (f"[{timestamp}] [RAW] | {msg.topic()} | "
                   f"Error parsing: {e} | Raw: {msg.value()}")
    
    def run(self) -> None:
        """
        Run the monitor main loop.
        
        Continuously polls for messages and displays them until stopped.
        """
        logging.info("[MONITOR] Starting Kafka monitor for topics: %s", self.topics)
        logging.info("Press Ctrl+C to stop monitoring...")
        print()  # Add blank line for readability
        
        message_count = 0
        
        try:
            while self.running:
                # Poll for messages
                msg = self.consumer.poll(1.0)
                
                if msg is None:
                    continue
                
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        logging.error("Kafka error: %s", msg.error())
                    continue
                
                # Display message
                formatted_msg = self._format_message(msg)
                print(formatted_msg)
                message_count += 1
                
                # Commit offset
                self.consumer.commit(asynchronous=True)
        
        except KeyboardInterrupt:
            logging.info("Monitor interrupted by user")
        
        finally:
            logging.info("[MONITOR] Total messages monitored: %d", message_count)
            self.consumer.close()
            logging.info("[MONITOR] Kafka monitor stopped")


def main():
    """
    Main entry point for the Kafka monitor CLI.
    """
    parser = argparse.ArgumentParser(
        description="Monitor Kafka topics written by Diana engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Monitor all engine output topics
  diana-monitor --config experiments.yaml
  
  # Monitor specific topics only
  diana-monitor --config experiments.yaml --topics ab-posteriors ab-metrics
  
  # Start monitoring from beginning of topics
  diana-monitor --config experiments.yaml --from-beginning
        """)
    
    parser.add_argument("--config", required=True,
                        help="path to experiments.yaml")
    parser.add_argument("--topics", nargs="+",
                        help="specific topics to monitor (default: all engine output topics)")
    parser.add_argument("--from-beginning", action="store_true",
                        help="start monitoring from beginning of topics (default: latest)")
    parser.add_argument("--consumer-group", default="diana_monitor",
                        help="Kafka consumer group ID (default: diana_monitor)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], 
                        default="INFO", help="logging level")
    
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S")

    # Load configuration
    try:
        cfg = load_config(Path(args.config))
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        return 1
    
    # Get Kafka configuration
    infra = cfg.get("infrastructure", {})
    kafka_cfg = infra.get("kafka", {})
    
    if not kafka_cfg:
        logging.error("No Kafka configuration found in experiments.yaml")
        return 1
    
    # Determine topics to monitor
    if args.topics:
        topics = args.topics
        logging.info("Using specified topics: %s", topics)
    else:
        # Default to all engine output topics
        kafka_topics = kafka_cfg.get("topics", {})
        logging.debug("Available Kafka topics in config: %s", kafka_topics)
        
        topics = [
            kafka_topics.get("posteriors_out", "ab-posteriors"),
            kafka_topics.get("metrics_out", "ab-metrics"),
            # Note: decisions_out not included as engine doesn't write to it yet
        ]
        # Remove any None values
        topics = [t for t in topics if t]
        logging.info("Auto-detected topics to monitor: %s", topics)
    
    if not topics:
        logging.error("No topics specified and none found in configuration")
        return 1
    
    # Create broker configuration
    brokers = kafka_cfg.get("brokers", ["localhost:9092"])
    broker_config = {"bootstrap.servers": ",".join(brokers)}
    logging.info("Using Kafka brokers: %s", brokers)
    
    # Adjust consumer group for from-beginning mode
    consumer_group = args.consumer_group
    if args.from_beginning:
        # Use a unique consumer group to start from beginning
        import time
        consumer_group = f"{args.consumer_group}_{int(time.time())}"
    
    # Create and run monitor
    try:
        monitor = KafkaMonitor(
            broker_config=broker_config,
            topics=topics,
            consumer_group=consumer_group
        )
        
        # Adjust offset behavior for from-beginning
        if args.from_beginning:
            monitor.consumer = Consumer({
                **broker_config,
                "group.id": consumer_group,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": True,
            })
            monitor.consumer.subscribe(topics)
        
        monitor.run()
        
    except Exception as e:
        logging.error(f"Monitor error: {e}", exc_info=True)
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())