#!/usr/bin/env python3
"""
Command-line interface for generating synthetic traffic.
"""

import argparse
import random
import time
import uuid
import logging
from pathlib import Path

from diana.utils.config import load_config
from diana.utils.kafka import to_json
from confluent_kafka import Producer

def main():
    """
    Main entry point for the traffic generator CLI.
    """
    ap = argparse.ArgumentParser(description="Generate synthetic A/B test traffic")
    ap.add_argument("--config", required=True, 
                   help="path to experiments.yaml")
    ap.add_argument("--eps", type=int, default=20,
                   help="ButtonDisplayed events per second (default 20)")
    ap.add_argument("--duration", type=int, default=60,
                   help="run time in seconds (default 60)")
    ap.add_argument("--prob", default="control=0.4,treatment=0.4",
                   help="CSV map variant=click_prob (0‑1)")
    ap.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], 
                   default="INFO", help="logging level")
    args = ap.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(message)s", 
        datefmt="%H:%M:%S")

    # Load configuration
    cfg = load_config(Path(args.config))
    infra = cfg["infrastructure"]
    brokers = ",".join(infra["kafka"]["brokers"])
    topic = infra["kafka"]["topics"]["raw_events_in"]

    # Create Kafka producer
    producer = Producer({"bootstrap.servers": brokers})

    # First Bayesian test in the file
    test = next(t for t in cfg["tests"]
                if t["algorithm"]["type"] == "bayesian")
    variants = [v["name"] for v in test["variants"]]

    # Map variant → click‑through probability
    prob_map = {k: float(v) for k, v in
                (pair.split("=") for pair in args.prob.split(","))}

    # Main loop
    end_time = time.time() + args.duration
    total_disp = 0

    logging.info("Publishing to %s  (brokers=%s)  for %s s",
                 topic, brokers, args.duration)

    try:
        while time.time() < end_time:
            loop_start = time.time()

            for _ in range(args.eps):
                user_id = uuid.uuid4().hex
                variant = random.choice(variants)
                now_ms = int(time.time() * 1000)

                # Exposure event
                display = {"event_name": "ButtonDisplayed",
                           "test_id": test["id"],
                           "variant": variant,
                           "user_id": user_id,
                           "timestamp": now_ms}
                producer.produce(topic, to_json(display))

                # Success event with probability p
                if random.random() < prob_map.get(variant, 0.4):
                    click = {"event_name": "ButtonClicked",
                             "test_id": test["id"],
                             "variant": variant,
                             "user_id": user_id,
                             "timestamp": now_ms + random.randint(50, 800)}
                    producer.produce(topic, to_json(click))

                total_disp += 1

            # Let librdkafka poll delivery callbacks
            producer.poll(0)

            # Throttle to maintain EPS
            spend = time.time() - loop_start
            if spend < 1:
                time.sleep(1 - spend)

    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    finally:
        producer.flush()
        logging.info("Finished – sent %s ButtonDisplayed events", total_disp)
    
    return 0

if __name__ == "__main__":
    exit(main())