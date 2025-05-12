#!/usr/bin/env python3
"""
traffic_generator.py  –  Synthetic event publisher (Confluent Kafka)

Features
--------
• Reads experiments.yaml, finds the first Bayesian test.
• Emits ButtonDisplayed for a random user/variant at EPS (events/sec).
• With user‑defined probability per variant, emits a follow‑up ButtonClicked.
• Stops cleanly after --duration seconds, flushing all Kafka buffers.

Example
-------
python traffic_generator.py \
        --config experiments.yaml \
        --eps 50 \
        --duration 6000 \
        --prob "control=0.35,treatment=0.55"
"""

import argparse, random, time, uuid, logging, json
from pathlib import Path
from datetime import datetime, timezone

import yaml, ujson as fast_json
from confluent_kafka import Producer


# ─────────── utility helpers ──────────────────────────────────────────────
def load_yaml(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)

def to_json(obj) -> bytes:
    try:
        return fast_json.dumps(obj).encode()
    except Exception:
        return json.dumps(obj).encode()


# ─────────── main publishing loop ─────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="experiments.yaml")
    ap.add_argument("--eps", type=int, default=20,
                    help="ButtonDisplayed events per second (default 20)")
    ap.add_argument("--duration", type=int, default=60,
                    help="run time in seconds (default 60)")
    ap.add_argument("--prob", default="control=0.4,treatment=0.4",
                    help="CSV map variant=click_prob (0‑1)")
    args = ap.parse_args()

    cfg        = load_yaml(Path(args.config))
    infra      = cfg["infrastructure"]
    brokers    = ",".join(infra["kafka"]["brokers"])
    topic      = infra["kafka"]["topics"]["raw_events_in"]

    producer   = Producer({"bootstrap.servers": brokers})

    # First Bayesian test in the file
    test       = next(t for t in cfg["tests"]
                      if t["algorithm"]["type"] == "bayesian")
    variants   = [v["name"] for v in test["variants"]]

    # Map variant → click‑through probability
    prob_map   = {k: float(v) for k, v in
                  (pair.split("=") for pair in args.prob.split(","))}

    # Basic logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    end_time   = time.time() + args.duration
    total_disp = 0

    logging.info("Publishing to %s  (brokers=%s)  for %s s",
                 topic, brokers, args.duration)

    while time.time() < end_time:
        loop_start = time.time()

        for _ in range(args.eps):
            user_id  = uuid.uuid4().hex
            variant  = random.choice(variants)
            now_ms   = int(time.time() * 1000)

            # Exposure event
            display  = {"event_name": "ButtonDisplayed",
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

    producer.flush()
    logging.info("Finished – sent %s ButtonDisplayed events", total_disp)


if __name__ == "__main__":
    main()
