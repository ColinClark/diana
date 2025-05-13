#!/usr/bin/env python3
"""
Core Bayesian engine implementation for Diana.

This module contains the main engine that processes events,
updates Beta posteriors, and emits results to various sinks.
"""

import json
import time
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from diana.engine.stores.base import StoreManager
from diana.utils.kafka import KafkaManager
from diana.utils.config import iso_now

class BayesianEngine:
    """
    Real-time Bayesian A/B testing engine.
    
    Consumes events from Kafka, updates Beta posteriors for each test variant,
    and outputs results to configurable sinks (console, CSV, or DynamoDB).
    """
    
    def __init__(self, store: StoreManager, kafka: KafkaManager, 
                 cfg: Dict[str, Any], run_secs: int, tick: int):
        """
        Initialize the Bayesian Engine.
        
        Args:
            store: Storage manager for posterior data
            kafka: Kafka manager for event streaming
            cfg: Configuration dictionary
            run_secs: Wall-clock seconds to run before auto-shutdown
            tick: Seconds between posterior updates and metrics
        """
        # Store dependencies
        self.store = store
        self.kafka = kafka
        
        # Test catalog
        self.tests = {t["id"]: t for t in cfg["tests"]
                     if t.get("algorithm", {}).get("type") == "bayesian"}
        
        # State & misc
        self.run_secs = run_secs
        self.tick = tick
        self.start_time = time.time()
        self.last_tick = time.time()
        self._lock = threading.Lock()
        
        # Performance metrics
        self.metrics = {
            "events_processed": 0,
            "events_skipped": 0,
            "processing_errors": 0,
            "kafka_errors": 0,
            "store_errors": 0,
            "processing_time_ms": [],
        }
        
        # Posterior & metric counters
        self.alpha = defaultdict(lambda: defaultdict(lambda: 1))
        self.beta = defaultdict(lambda: defaultdict(lambda: 1))
        self.exposures = defaultdict(lambda: defaultdict(int))
        self.successes = defaultdict(lambda: defaultdict(int))
        self.inc_exp = defaultdict(lambda: defaultdict(int))
        self.inc_suc = defaultdict(lambda: defaultdict(int))
    
    def _in_window(self, test: Dict[str, Any], ts: datetime) -> bool:
        """
        Check if timestamp is within test window.
        
        Args:
            test: Test configuration
            ts: Event timestamp
            
        Returns:
            bool: True if within test window, False otherwise
        """
        # For now, just return True (window checks disabled)
        # In production, uncomment this logic to enforce time windows:
        # 
        # start = datetime.fromisoformat(test["start_time"].replace("Z","+00:00"))
        # end = datetime.fromisoformat(
        #   test.get("end_time","9999-01-01T00:00:00+00:00").replace("Z","+00:00"))
        # return start <= ts <= end
        return True
    
    def run(self) -> None:
        """
        Run the Bayesian engine main loop.
        
        Processes events until run_secs has elapsed.
        """
        logging.info("Engine start: run_secs=%s tick=%s",
                    self.run_secs, self.tick)
        
        while (time.time() - self.start_time) < self.run_secs:
            # Poll for messages
            msg = self.kafka.poll_messages(1.0)
            
            if msg is None:
                self._maybe_tick()
                continue
                
            # Process message
            try:
                event = json.loads(msg.value())
                self._process(event)
                self.kafka.commit(asynchronous=True)
            except Exception as e:
                logging.error(f"Error processing message: {e}", exc_info=True)
                self.metrics["processing_errors"] += 1
        
        # Clean shutdown
        self.kafka.flush(5)
        logging.info("Engine finished.")
    
    def _process(self, ev: Dict[str, Any]) -> None:
        """
        Process a single event.
        
        Updates posterior parameters and counters based on event type.
        
        Args:
            ev: Event dictionary
        """
        start_time = time.time()
        
        try:
            # Get test ID and skip if not in catalog
            tid = ev.get("test_id")
            if tid not in self.tests:
                self.metrics["events_skipped"] += 1
                return
                
            test = self.tests[tid]
            
            # Parse timestamp and check window
            ts = datetime.fromtimestamp(ev["timestamp"]/1000, tz=timezone.utc)
            if not self._in_window(test, ts):
                self.metrics["events_skipped"] += 1
                return
            
            # Get variant and event name
            var = ev.get("variant")
            e_name = ev["event_name"]
            
            # Update counters under lock
            with self._lock:
                if e_name.endswith("Displayed"):
                    self.exposures[tid][var] += 1
                    self.inc_exp[tid][var] += 1
                    # Update beta only on display events
                    self.beta[tid][var] = (
                        1 + self.exposures[tid][var] - self.successes[tid][var])
                elif e_name.endswith("Clicked"):
                    self.successes[tid][var] += 1
                    self.inc_suc[tid][var] += 1
                    self.alpha[tid][var] += 1
                else:
                    self.metrics["events_skipped"] += 1
                    return
                    
            self.metrics["events_processed"] += 1
            
        finally:
            # Track processing time
            elapsed_ms = (time.time() - start_time) * 1000
            self.metrics["processing_time_ms"].append(elapsed_ms)
    
    def _maybe_tick(self) -> None:
        """
        Check if it's time for a periodic update.
        
        Emits posterior snapshots and metrics if tick interval has elapsed.
        """
        now = time.time()
        if now - self.last_tick < self.tick:
            return
            
        self.last_tick = now
        
        with self._lock:
            for tid, variants in self.exposures.items():
                for v, exp_tot in variants.items():
                    # Get counters
                    suc_tot = self.successes[tid][v]
                    exp_inc = self.inc_exp[tid].pop(v, 0)
                    suc_inc = self.inc_suc[tid].pop(v, 0)
                    alpha = self.alpha[tid][v]
                    beta = self.beta[tid][v]
                    ts = iso_now()
                    
                    # Posterior snapshot to Kafka
                    post_msg = {
                        "test_id": tid, 
                        "variant": v,
                        "timestamp": ts, 
                        "alpha": alpha, 
                        "beta": beta
                    }
                    self.kafka.publish("posteriors_out", post_msg)
                    
                    # Store posterior update
                    try:
                        self.store.put(tid, v, alpha, beta, ts)
                    except Exception as e:
                        logging.error(f"Error writing to store: {e}")
                        self.metrics["store_errors"] += 1
                    
                    # Progress metrics to Kafka
                    prog_msg = {
                        "test_id": tid, 
                        "variant": v,
                        "timestamp": ts,
                        "exposures_inc": exp_inc,
                        "successes_inc": suc_inc,
                        "exposures_total": exp_tot,
                        "successes_total": suc_tot,
                        "success_rate_total": round(suc_tot / exp_tot, 4) if exp_tot else 0.0
                    }
                    self.kafka.publish("metrics_out", prog_msg)
            
            # Include health metrics in progress messages
            if self.metrics["processing_time_ms"]:
                avg_proc_time = sum(self.metrics["processing_time_ms"]) / len(self.metrics["processing_time_ms"])
                health_msg = {
                    "timestamp": iso_now(),
                    "events_processed": self.metrics["events_processed"],
                    "events_skipped": self.metrics["events_skipped"],
                    "avg_processing_time_ms": avg_proc_time,
                    "error_count": self.metrics["processing_errors"] + self.metrics["store_errors"],
                }
                self.kafka.publish("metrics_out", health_msg)
                self.metrics["processing_time_ms"] = []  # Reset for next window