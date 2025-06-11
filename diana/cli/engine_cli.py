#!/usr/bin/env python3
"""
Command-line interface for the Bayesian engine.
"""

import argparse
import logging
from pathlib import Path

from diana.utils.config import load_config
from diana.utils.kafka import KafkaManager
from diana.engine.stores.base import StoreManager
from diana.engine.bayesian import BayesianEngine

def main():
    """
    Main entry point for the Bayesian engine CLI.
    """
    parser = argparse.ArgumentParser(description="Diana Bayesian A/B Testing Engine")
    parser.add_argument("--config", required=True,
                        help="path to experiments.yaml")
    parser.add_argument("--run-for", type=int, default=3600,
                        help="wall‑clock seconds to run")
    parser.add_argument("--progress-interval", type=int, default=60,
                        help="seconds between progress ticks")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], 
                        default="INFO", help="logging level")
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S")

    # Load configuration
    cfg = load_config(Path(args.config))
    
    # Get output sink
    sink_type = cfg.get("output_sink", "console").lower()
    
    # Create store manager
    store = StoreManager.create(sink_type, cfg)
    
    # Create Kafka manager
    infra = cfg["infrastructure"]
    kafka_config = {"bootstrap.servers": ",".join(infra["kafka"]["brokers"])}
    kafka = KafkaManager(
        broker_config=kafka_config, 
        topics=infra["kafka"]["topics"],
        consumer_group="ab_engine"
    )
    
    # Log loaded test configuration
    bayesian_tests = [t["id"] for t in cfg.get("tests", []) 
                     if t.get("algorithm", {}).get("type") == "bayesian"]
    logging.info("Diana engine starting with tests: %s", bayesian_tests)
    
    # Create and run engine
    engine = BayesianEngine(
        store=store,
        kafka=kafka,
        cfg=cfg,
        run_secs=args.run_for,
        tick=args.progress_interval
    )
    
    try:
        engine.run()
    except KeyboardInterrupt:
        logging.info("Shutting down gracefully...")
    except Exception as e:
        logging.error(f"Engine error: {e}", exc_info=True)
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())