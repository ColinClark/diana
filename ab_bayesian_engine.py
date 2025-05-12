#!/usr/bin/env python3
"""
Real‑time Bayesian AB‑engine with pluggable sinks
------------------------------------------------
• Consumes raw events  ➜ updates Beta(α, β) posteriors.
• Publishes snapshots  ➜ console | DynamoDB | CSV  +   Kafka.
• Emits incremental & total progress to Kafka every N seconds.
"""

import json, time, argparse, threading, logging, csv, os
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

import yaml, boto3, ujson as fast_json
from confluent_kafka import Producer, Consumer, KafkaError

# ────────────────── helpers ────────────────────────────────────────────────
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

# ────────────────── sink helpers ───────────────────────────────────────────
class PosteriorStoreDynamo:
    """Writes posterior rows into a DynamoDB table."""
    def __init__(self, table_name: str, region: str):
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def put(self, test_id: str, variant: str, alpha: int, beta: int, ts: str):
        self.table.put_item(Item=dict(
            test_id=test_id, variant=variant,
            timestamp=ts, alpha=alpha, beta=beta))

class PosteriorStoreCSV:
    """Appends posterior rows to a CSV file (creates header once)."""
    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.file_path.open('w', newline='') as f:
                csv.writer(f).writerow(
                    ['timestamp', 'test_id', 'variant', 'alpha', 'beta'])

    def put(self, test_id: str, variant: str, alpha: int, beta: int, ts: str):
        with self.file_path.open('a', newline='') as f:
            csv.writer(f).writerow([ts, test_id, variant, alpha, beta])

# ────────────────── core engine ────────────────────────────────────────────
class BayesianEngine:
    def __init__(self, cfg: dict, run_secs: int, tick: int):
        infra            = cfg["infrastructure"]
        kafka_cfg        = {"bootstrap.servers": ",".join(infra["kafka"]["brokers"])}
        self.sink        = cfg.get("output_sink", "dynamodb").lower()
        self.csv_path    = Path(cfg.get("csv_output_path", "posteriors.csv"))

        # Kafka
        self.producer = Producer(kafka_cfg)
        self.consumer = Consumer({
            **kafka_cfg,
            "group.id": "ab_engine",
            "auto.offset.reset": "earliest",
        })
        self.consumer.subscribe([infra["kafka"]["topics"]["raw_events_in"]])

        # Choose sink
        if self.sink == "dynamodb":
            dynamo = infra["dynamodb"]
            self.store = PosteriorStoreDynamo(
                table_name=dynamo["tables"]["posteriors"],
                region=dynamo["region"])
        elif self.sink == "csv":
            self.store = PosteriorStoreCSV(self.csv_path)
        elif self.sink == "console":
            self.store = None  # handled inline
        else:
            raise ValueError(f"Unknown output_sink: {self.sink}")

        # Test catalog
        self.tests      = {t["id"]: t for t in cfg["tests"]
                           if t.get("algorithm", {}).get("type") == "bayesian"}

        # State & misc
        self.run_secs       = run_secs
        self.tick           = tick
        self.start_time     = time.time()
        self.last_tick      = time.time()
        self._lock          = threading.Lock()

        # Posterior & metric counters
        self.alpha = defaultdict(lambda: defaultdict(lambda: 1))
        self.beta  = defaultdict(lambda: defaultdict(lambda: 1))
        self.exposures = defaultdict(lambda: defaultdict(int))
        self.successes = defaultdict(lambda: defaultdict(int))
        self.inc_exp   = defaultdict(lambda: defaultdict(int))
        self.inc_suc   = defaultdict(lambda: defaultdict(int))

        topics = infra["kafka"]["topics"]
        self.metrics_topic = topics["metrics_out"]
        self.post_topic    = topics["posteriors_out"]

    # ────────── utility ------------------------------------------------------
    def _in_window(self, test, ts: datetime) -> bool:
        # start = datetime.fromisoformat(test["start_time"].replace("Z","+00:00"))
        # end   = datetime.fromisoformat(
        #   test.get("end_time","9999-01-01T00:00:00+00:00").replace("Z","+00:00"))
        # return start <= ts <= end
        return True

    # ────────── main loop ----------------------------------------------------
    def run(self):
        logging.info("Engine start: sink=%s run_secs=%s tick=%s",
                     self.sink, self.run_secs, self.tick)
        while (time.time() - self.start_time) < self.run_secs:
            msg = self.consumer.poll(1.0)
            if msg is None:
                self._maybe_tick()
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logging.error("Kafka error: %s", msg.error())
                continue
            self._process(json.loads(msg.value()))
            self.consumer.commit(asynchronous=True)

        self.producer.flush(5)
        logging.info("Engine finished.")

    # ────────── per‑event processing ----------------------------------------
    def _process(self, ev):
        tid = ev.get("test_id")
        if tid not in self.tests:
            return
        test = self.tests[tid]
        ts   = datetime.fromtimestamp(ev["timestamp"]/1000, tz=timezone.utc)
        if not self._in_window(test, ts):
            return

        var = ev.get("variant")
        e_name = ev["event_name"]

        with self._lock:
            if e_name.endswith("Displayed"):
                self.exposures[tid][var] += 1
                self.inc_exp[tid][var]  += 1
            elif e_name.endswith("Clicked"):
                self.successes[tid][var] += 1
                self.inc_suc[tid][var]   += 1
                self.alpha[tid][var]    += 1
            else:
                return
            self.beta[tid][var] = (
                1 + self.exposures[tid][var] - self.successes[tid][var])

    # ────────── periodic tick ------------------------------------------------
    def _maybe_tick(self):
        now = time.time()
        if now - self.last_tick < self.tick:
            return
        self.last_tick = now

        with self._lock:
            for tid, variants in self.exposures.items():
                for v, exp_tot in variants.items():
                    suc_tot = self.successes[tid][v]
                    exp_inc = self.inc_exp[tid].pop(v, 0)
                    suc_inc = self.inc_suc[tid].pop(v, 0)
                    alpha   = self.alpha[tid][v]
                    beta    = self.beta[tid][v]
                    ts      = iso_now()

                    # Posterior snapshot to Kafka
                    post_msg = {"test_id": tid, "variant": v,
                                "timestamp": ts, "alpha": alpha, "beta": beta}
                    self.producer.produce(self.post_topic, to_json(post_msg))

                    # Sink‑specific persistence
                    if self.sink == "dynamodb" or self.sink == "csv":
                        self.store.put(tid, v, alpha, beta, ts)
                    else:  # console
                        logging.info("Posterior ► %s", post_msg)

                    # Progress metrics payload
                    prog_msg = {"test_id": tid, "variant": v,
                                "timestamp": ts,
                                "exposures_inc": exp_inc,
                                "successes_inc": suc_inc,
                                "exposures_total": exp_tot,
                                "successes_total": suc_tot,
                                "success_rate_total":
                                    round(suc_tot / exp_tot, 4) if exp_tot else 0.0}
                    self.producer.produce(self.metrics_topic, to_json(prog_msg))

            self.producer.poll(0)

# ────────────────── CLI ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True,
                        help="path to experiments.yaml")
    parser.add_argument("--run-for", type=int, default=3600,
                        help="wall‑clock seconds to run")
    parser.add_argument("--progress-interval", type=int, default=60,
                        help="seconds between progress ticks")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S")

    cfg = load_config(Path(args.config))
    engine = BayesianEngine(cfg, args.run_for, args.progress_interval)
    engine.run()

if __name__ == "__main__":
    main()
