#!/usr/bin/env python3
"""
Real‑time Bayesian AB‑engine using Confluent Kafka client
--------------------------------------------------------
 • Consumes raw events  ➜ updates Beta(α, β) posteriors
 • Publishes snapshots  ➜ DynamoDB  & Kafka
 • Emits incremental / total progress every N seconds
"""

import json, time, argparse, threading, logging
from operator import truediv
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import yaml, boto3, ujson as fast_json
from confluent_kafka import Producer, Consumer, KafkaError

# ---------- helpers ----------------------------------------------------------
def iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")

def load_config(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)

def to_json(obj) -> bytes:
    try:
        return fast_json.dumps(obj).encode()
    except Exception:
        return json.dumps(obj).encode()

# ---------- posterior store (DynamoDB) ---------------------------------------
class PosteriorStore:
    def __init__(self, table_name: str, region: str):
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def put(self, test_id: str, variant: str, alpha: int, beta: int, ts: str):
        self.table.put_item(Item=dict(
            test_id=test_id, variant=variant,
            timestamp=ts, alpha=alpha, beta=beta))

# ---------- main engine ------------------------------------------------------
class BayesianEngine:
    def __init__(self, cfg: dict, run_secs: int, tick: int):
        infra     = cfg["infrastructure"]
        kafka_cfg = {"bootstrap.servers": ",".join(infra["kafka"]["brokers"])}

        # Confluent Producer / Consumer
        self.producer = Producer(kafka_cfg)
        self.consumer = Consumer({
            **kafka_cfg,
            "group.id": "ab_engine",
            "auto.offset.reset": "earliest",
        })
        self.consumer.subscribe([infra["kafka"]["topics"]["raw_events_in"]])

        # DynamoDB
        dynamo = infra["dynamodb"]
        self.store = PosteriorStore(
            table_name=dynamo["tables"]["posteriors"],
            region=dynamo["region"])

        self.tests           = self._index_tests(cfg["tests"])
        self.start_time      = time.time()
        self.run_secs        = run_secs
        self.tick            = tick
        self.last_tick       = time.time()
        self._lock           = threading.Lock()

        # α/β per test→variant
        self.alpha = defaultdict(lambda: defaultdict(lambda: 1))
        self.beta  = defaultdict(lambda: defaultdict(lambda: 1))
        # cumulative counts
        self.exposures = defaultdict(lambda: defaultdict(int))
        self.successes = defaultdict(lambda: defaultdict(int))
        # incremental counts (since last tick)
        self.inc_exp = defaultdict(lambda: defaultdict(int))
        self.inc_suc = defaultdict(lambda: defaultdict(int))

        self.metrics_topic   = infra["kafka"]["topics"]["metrics_out"]
        self.post_topic      = infra["kafka"]["topics"]["posteriors_out"]

    # -------- test index & helpers -------------------------------------------
    @staticmethod
    def _index_tests(tests):
        return {t["id"]: t for t in tests
                if t.get("algorithm", {}).get("type") == "bayesian"}

    def _in_window(self, test, ts: datetime):
        #start = datetime.fromisoformat(test["start_time"].replace("Z","+00:00"))
        #end   = datetime.fromisoformat(
         #   test.get("end_time","9999-01-01T00:00:00+00:00").replace("Z","+00:00"))
        #return start <= ts <= end
        return True
    # -------- consume loop ---------------------------------------------------
    def run(self):
        logging.info("Engine starting; run_secs=%s tick=%s", self.run_secs, self.tick)
        while (time.time() - self.start_time) < self.run_secs:
            msg = self.consumer.poll(1.0)
            if msg is None:                    # no data
                self._maybe_tick()
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logging.error("Kafka error: %s", msg.error())
                continue
            print("WebEvent:", msg.value)
            print("WebEventJSON:", json.loads(msg.value()))
            self._process(json.loads(msg.value()))
            self.consumer.commit(asynchronous=True)

        logging.info("Run‑duration reached, flushing producer …")
        self.producer.flush(5)

    # -------- event processing ----------------------------------------------
    def _process(self, ev):
        print(ev)
        test_id  = ev.get("test_id")
        if test_id not in self.tests:
            return                          # irrelevant experiment

        test     = self.tests[test_id]
        ts       = datetime.fromtimestamp(ev["timestamp"]/1000, tz=timezone.utc)
        if not self._in_window(test, ts):
            return

        variant  = ev.get("variant")
        e_name   = ev["event_name"]

        with self._lock:
            if e_name.endswith("Displayed"):
                self.exposures[test_id][variant] += 1
                self.inc_exp[test_id][variant]   += 1
            elif e_name.endswith("Clicked"):
                # Bernoulli success
                self.successes[test_id][variant] += 1
                self.inc_suc[test_id][variant]   += 1
                self.alpha[test_id][variant]    += 1
            else:
                return
            self.beta[test_id][variant] = (
                1 + self.exposures[test_id][variant]
                - self.successes[test_id][variant])

    # -------- progress tick --------------------------------------------------
    def _maybe_tick(self):
        now = time.time()
        if now - self.last_tick < self.tick:
            return
        self.last_tick = now

        with self._lock:
            payloads = []
            for tid, variants in self.exposures.items():
                for v, exp_tot in variants.items():
                    suc_tot = self.successes[tid][v]
                    exp_inc = self.inc_exp[tid].pop(v, 0)
                    suc_inc = self.inc_suc[tid].pop(v, 0)
                    alpha   = self.alpha[tid][v]
                    beta    = self.beta[tid][v]
                    ts      = iso_now()

                    # --- publish posterior snapshot -------------------------
                    post_msg = {
                        "test_id": tid, "variant": v, "timestamp": ts,
                        "alpha": alpha, "beta": beta}
                    self.producer.produce(
                        self.post_topic, to_json(post_msg))
                    self.store.put(tid, v, alpha, beta, ts)

                    # --- publish progress metrics --------------------------
                    prog_msg = {
                        "test_id": tid, "variant": v, "timestamp": ts,
                        "exposures_inc": exp_inc, "successes_inc": suc_inc,
                        "exposures_total": exp_tot, "successes_total": suc_tot,
                        "success_rate_total":
                            round(suc_tot / exp_tot, 4) if exp_tot else 0.0
                    }
                    payloads.append(prog_msg)
                    self.producer.produce(
                        self.metrics_topic, to_json(prog_msg))

            if payloads:
                logging.info("Progress tick: %s", payloads)
            self.producer.poll(0)

# ---------- CLI --------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-for", type=int, default=3600,
                        help="wall‑clock seconds to run before exit")
    parser.add_argument("--progress-interval", type=int, default=60,
                        help="seconds between progress payloads")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S")

    cfg = load_config(Path(args.config))
    eng = BayesianEngine(cfg, args.run_for, args.progress_interval)
    eng.run()

if __name__ == "__main__":
    main()
