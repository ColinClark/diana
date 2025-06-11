#!/usr/bin/env python3
"""
Command-line interface for dynamic traffic routing based on A/B test metrics.

This module implements a real-time routing simulator that reads metrics from Kafka
and dynamically adjusts traffic allocation based on performance indicators.
"""

import argparse
import json
import logging
import signal
import time
import threading
import random
import sys
import select
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
from collections import defaultdict, deque

from confluent_kafka import Consumer, Producer, KafkaError
from scipy.stats import beta
import numpy as np

from diana.utils.config import load_config, iso_now


class DynamicRouter:
    """
    Dynamic traffic router that adjusts allocation based on real-time metrics.
    
    Uses Thompson Sampling and Multi-Armed Bandit algorithms to optimize
    traffic allocation in real-time based on observed performance.
    
    Architecture:
    - Kafka Consumer Thread: Continuously reads metrics from Kafka and updates shared state
    - Routing Thread: Simulates traffic routing decisions using current metrics
    - Main Thread: Coordinates both threads and handles display updates
    """
    
    def __init__(self, kafka_config: Dict[str, str], topics: Dict[str, str], 
                 routing_config: Dict[str, Any], tests_config: Dict[str, Any]):
        """
        Initialize the dynamic router.
        
        Args:
            kafka_config: Kafka broker configuration
            topics: Kafka topic names
            routing_config: Routing algorithm configuration
            tests_config: Test configuration
        """
        self.routing_config = routing_config
        self.tests_config = tests_config
        self.running = True
        
        # Kafka setup
        consumer_config = {
            **kafka_config,
            "group.id": "diana_router",
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        }
        
        self.consumer = Consumer(consumer_config)
        self.producer = Producer(kafka_config)
        
        # Subscribe to metrics topics
        metrics_topics = [topics.get("metrics_out"), topics.get("posteriors_out")]
        metrics_topics = [t for t in metrics_topics if t]
        self.consumer.subscribe(metrics_topics)
        
        self.routing_topic = topics.get("routing_decisions", "routing-decisions")
        
        # Shared state with thread synchronization
        self.data_lock = threading.RLock()  # Protects all shared data structures
        self.test_metrics = defaultdict(lambda: defaultdict(dict))
        self.current_allocations = defaultdict(dict)
        self.performance_history = defaultdict(lambda: defaultdict(lambda: deque(maxlen=100)))
        self.last_metrics_update = defaultdict(float)  # Track when each test was last updated
        
        # Algorithm parameters
        self.algorithm = routing_config.get("algorithm", "thompson_sampling")
        self.update_interval = routing_config.get("update_interval_seconds", 30)
        self.min_samples = routing_config.get("min_samples_per_variant", 100)
        self.confidence_threshold = routing_config.get("confidence_threshold", 0.95)
        self.exploration_rate = routing_config.get("exploration_rate", 0.1)
        
        # Traffic simulation parameters
        self.simulate_traffic = routing_config.get("simulate_traffic", True)
        self.routing_request_interval = routing_config.get("routing_request_interval", 5.0)
        self.display_results = routing_config.get("display_results", False)
        
        # Display state
        self.routing_stats = defaultdict(lambda: {"total_requests": 0, "variant_counts": defaultdict(int)})
        self.last_display_update = time.time()
        self.display_lock = threading.Lock()
        
        # Thread management
        self.kafka_thread = None
        self.routing_thread = None
        self.simulation_thread = None
        self.last_routing_update = time.time()
        
        # Statistics
        self.metrics_messages_processed = 0
        self.routing_decisions_made = 0
        
        # Signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logging.info("[ROUTER] Received shutdown signal, stopping router...")
        self.running = False
    
    def _calculate_thompson_allocation(self, test_id: str, variants: Dict[str, Dict]) -> Dict[str, float]:
        """
        Calculate optimal allocation using Thompson Sampling.
        
        Args:
            test_id: Test identifier
            variants: Dictionary of variant metrics
            
        Returns:
            Dictionary of variant allocations (sum to 1.0)
        """
        if len(variants) < 2:
            return {}
        
        # Sample from posterior distributions
        samples = {}
        for variant, metrics in variants.items():
            alpha = metrics.get("alpha", 1)
            beta_param = metrics.get("beta", 1)
            
            # Sample conversion rate from Beta distribution
            if alpha > 0 and beta_param > 0:
                samples[variant] = beta.rvs(alpha, beta_param)
            else:
                samples[variant] = 0.5  # Default if no data
        
        # Thompson sampling: allocate more traffic to better performing variants
        if not samples:
            return {}
        
        # Calculate probabilities that each variant is best
        n_samples = 10000
        win_counts = defaultdict(int)
        
        for _ in range(n_samples):
            variant_samples = {}
            for variant, metrics in variants.items():
                alpha = metrics.get("alpha", 1)
                beta_param = metrics.get("beta", 1)
                variant_samples[variant] = beta.rvs(alpha, beta_param)
            
            # Find the best variant in this sample
            best_variant = max(variant_samples.keys(), key=lambda v: variant_samples[v])
            win_counts[best_variant] += 1
        
        # Convert win probabilities to allocations with exploration
        total_wins = sum(win_counts.values())
        allocations = {}
        
        for variant in variants.keys():
            win_prob = win_counts[variant] / total_wins if total_wins > 0 else 1.0 / len(variants)
            
            # Add exploration component
            exploration_allocation = self.exploration_rate / len(variants)
            exploitation_allocation = (1 - self.exploration_rate) * win_prob
            
            allocations[variant] = exploration_allocation + exploitation_allocation
        
        # Normalize to ensure sum = 1.0
        total_allocation = sum(allocations.values())
        if total_allocation > 0:
            allocations = {v: alloc / total_allocation for v, alloc in allocations.items()}
        
        return allocations
    
    def _get_default_allocations(self, test_id: str) -> Dict[str, float]:
        """
        Get default equal allocations for a test when no data is available.
        
        Args:
            test_id: Test identifier
            
        Returns:
            Dictionary of equal allocations
        """
        # Find test configuration
        test_config = None
        for test in self.tests_config.get("tests", []):
            if test.get("id") == test_id:
                test_config = test
                break
        
        if not test_config:
            return {}
        
        # Get variants from configuration
        variants = test_config.get("variants", [])
        if not variants:
            return {}
        
        # Create equal allocations
        variant_names = [v.get("name") for v in variants if v.get("name")]
        if variant_names:
            equal_allocation = 1.0 / len(variant_names)
            return {name: equal_allocation for name in variant_names}
        
        return {}
    
    def _calculate_epsilon_greedy_allocation(self, test_id: str, variants: Dict[str, Dict]) -> Dict[str, float]:
        """
        Calculate allocation using Epsilon-Greedy algorithm.
        
        Args:
            test_id: Test identifier
            variants: Dictionary of variant metrics
            
        Returns:
            Dictionary of variant allocations
        """
        if len(variants) < 2:
            return {}
        
        # Calculate conversion rates
        conversion_rates = {}
        for variant, metrics in variants.items():
            alpha = metrics.get("alpha", 1)
            beta_param = metrics.get("beta", 1)
            total = alpha + beta_param
            conversion_rates[variant] = alpha / total if total > 0 else 0.0
        
        # Find best variant
        best_variant = max(conversion_rates.keys(), key=lambda v: conversion_rates[v])
        
        # Allocate traffic
        allocations = {}
        n_variants = len(variants)
        
        for variant in variants.keys():
            if variant == best_variant:
                # Best variant gets (1 - epsilon) + epsilon/n
                allocations[variant] = (1 - self.exploration_rate) + (self.exploration_rate / n_variants)
            else:
                # Other variants get epsilon/n
                allocations[variant] = self.exploration_rate / n_variants
        
        return allocations
    
    def _should_update_routing(self) -> bool:
        """Check if it's time to update routing decisions."""
        return (time.time() - self.last_routing_update) >= self.update_interval
    
    def _has_sufficient_data(self, variants: Dict[str, Dict]) -> bool:
        """Check if we have sufficient data to make routing decisions."""
        for variant, metrics in variants.items():
            alpha = metrics.get("alpha", 1)
            beta_param = metrics.get("beta", 1)
            total_samples = alpha + beta_param - 2  # Subtract priors
            
            if total_samples < self.min_samples:
                return False
        
        return True
    
    def _update_routing_decisions(self):
        """Calculate and publish new routing decisions using current metrics snapshot."""
        current_time = iso_now()
        self.last_routing_update = time.time()
        
        # Get thread-safe snapshot of current metrics
        metrics_snapshot = self._get_current_metrics_snapshot()
        
        for test_id, variants in metrics_snapshot.items():
            if len(variants) < 2:
                continue
            
            # Check if we have sufficient data
            if not self._has_sufficient_data(variants):
                logging.debug("[ROUTER] [%s] Insufficient data for routing decision, using defaults", test_id)
                # Use default equal allocations when insufficient data
                default_allocations = self._get_default_allocations(test_id)
                if default_allocations:
                    with self.data_lock:
                        if test_id not in self.current_allocations:
                            self.current_allocations[test_id] = default_allocations
                            logging.info("[ROUTER] [%s] Using default equal allocations: %s", 
                                       test_id, {v: f"{a:.3f}" for v, a in default_allocations.items()})
                continue
            
            # Calculate new allocations
            if self.algorithm == "thompson_sampling":
                new_allocations = self._calculate_thompson_allocation(test_id, variants)
            elif self.algorithm == "epsilon_greedy":
                new_allocations = self._calculate_epsilon_greedy_allocation(test_id, variants)
            else:
                logging.warning("[ROUTER] Unknown algorithm: %s", self.algorithm)
                continue
            
            if not new_allocations:
                continue
            
            # Thread-safe allocation update check
            with self.data_lock:
                old_allocations = self.current_allocations.get(test_id, {})
                significant_change = False
                
                for variant, new_alloc in new_allocations.items():
                    old_alloc = old_allocations.get(variant, 1.0 / len(new_allocations))
                    if abs(new_alloc - old_alloc) > 0.05:  # 5% threshold
                        significant_change = True
                        break
                
                if significant_change or not old_allocations:
                    # Update current allocations
                    self.current_allocations[test_id] = new_allocations
                    self.routing_decisions_made += 1
                    
                    # Create routing decision message
                    routing_msg = {
                        "test_id": test_id,
                        "timestamp": current_time,
                        "algorithm": self.algorithm,
                        "allocations": new_allocations,
                        "variant_metrics": {
                            variant: {
                                "conversion_rate": metrics.get("alpha", 1) / (metrics.get("alpha", 1) + metrics.get("beta", 1)),
                                "total_samples": metrics.get("alpha", 1) + metrics.get("beta", 1) - 2,
                                "confidence": self._calculate_confidence(metrics)
                            }
                            for variant, metrics in variants.items()
                        }
                    }
                    
                    # Publish routing decision
                    try:
                        self.producer.produce(
                            self.routing_topic,
                            json.dumps(routing_msg).encode('utf-8')
                        )
                        self.producer.poll(0)
                        
                        logging.info("[ROUTER] [%s] New allocations: %s", 
                                   test_id, 
                                   {v: f"{a:.3f}" for v, a in new_allocations.items()})
                        
                    except Exception as e:
                        logging.error("[ROUTER] Error publishing routing decision: %s", e)
    
    def _calculate_confidence(self, metrics: Dict[str, Any]) -> float:
        """Calculate confidence in the current metrics."""
        alpha = metrics.get("alpha", 1)
        beta_param = metrics.get("beta", 1)
        
        # Use the width of the credible interval as a proxy for confidence
        # Narrower interval = higher confidence
        if alpha > 0 and beta_param > 0:
            lo, hi = beta.ppf([0.025, 0.975], alpha, beta_param)
            interval_width = hi - lo
            # Convert to confidence score (0-1, where 1 is highest confidence)
            confidence = max(0, min(1, 1 - interval_width))
            return confidence
        
        return 0.0
    
    def _simulate_routing_request(self, test_id: str, allocations: Dict[str, float]) -> str:
        """
        Simulate a routing request using current allocations.
        
        Args:
            test_id: Test identifier
            allocations: Current variant allocations
            
        Returns:
            Selected variant name
        """
        if not allocations:
            return "control"  # Default fallback
        
        # Weighted random selection based on allocations
        variants = list(allocations.keys())
        weights = list(allocations.values())
        
        # Normalize weights to ensure they sum to 1
        total_weight = sum(weights)
        if total_weight > 0:
            weights = [w / total_weight for w in weights]
        else:
            weights = [1.0 / len(variants)] * len(variants)
        
        # Select variant using weighted random choice
        selected_variant = np.random.choice(variants, p=weights)
        
        # Update routing statistics if display is enabled
        if self.display_results:
            with self.display_lock:
                self.routing_stats[test_id]["total_requests"] += 1
                self.routing_stats[test_id]["variant_counts"][selected_variant] += 1
        
        return selected_variant
    
    def _traffic_simulation_loop(self):
        """
        Background thread that simulates routing requests using current allocations.
        This thread reads from shared data structures updated by the Kafka consumer.
        """
        logging.info("[ROUTER] Starting traffic simulation (interval: %.1fs)", self.routing_request_interval)
        
        while self.running:
            try:
                # Wait for the interval
                time.sleep(self.routing_request_interval)
                
                if not self.running:
                    break
                
                # Get current allocations snapshot (thread-safe)
                current_allocations = self._get_current_allocations_snapshot()
                
                # Generate routing requests for all active tests
                for test_id, allocations in current_allocations.items():
                    if allocations:
                        selected_variant = self._simulate_routing_request(test_id, allocations)
                        
                        # Create simulated routing request message
                        request_msg = {
                            "test_id": test_id,
                            "timestamp": iso_now(),
                            "request_type": "routing_request",
                            "selected_variant": selected_variant,
                            "current_allocations": allocations,
                            "algorithm": self.algorithm
                        }
                        
                        # Publish simulated request
                        try:
                            self.producer.produce(
                                self.routing_topic + "-requests",
                                json.dumps(request_msg).encode('utf-8')
                            )
                            self.producer.poll(0)
                            
                            # Display routing result if enabled
                            if self.display_results:
                                self._display_routing_result(test_id, selected_variant, allocations)
                            else:
                                logging.debug("[ROUTER] [%s] Simulated request → %s (weights: %s)", 
                                            test_id, selected_variant,
                                            {v: f"{w:.3f}" for v, w in allocations.items()})
                            
                        except Exception as e:
                            logging.error("[ROUTER] Error publishing simulated request: %s", e)
                
            except Exception as e:
                logging.error("[ROUTER] Error in traffic simulation: %s", e)
                if self.running:
                    time.sleep(1)  # Brief pause before retrying
        
        logging.info("[ROUTER] Traffic simulation stopped")
    
    def _display_routing_result(self, test_id: str, selected_variant: str, allocations: Dict[str, float]):
        """
        Display routing result in real-time format (thread-safe).
        
        Args:
            test_id: Test identifier
            selected_variant: Selected variant name
            allocations: Current allocations
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Get current metrics for the selected variant (thread-safe)
        with self.data_lock:
            variant_metrics = self.test_metrics.get(test_id, {}).get(selected_variant, {})
            # Make a copy to avoid threading issues
            variant_metrics = dict(variant_metrics) if variant_metrics else {}
        
        # Calculate conversion rate
        alpha = variant_metrics.get("alpha", 1)
        beta_param = variant_metrics.get("beta", 1)
        conversion_rate = alpha / (alpha + beta_param) if (alpha + beta_param) > 0 else 0.0
        total_samples = alpha + beta_param - 2  # Subtract priors
        
        # Get routing statistics (thread-safe)
        with self.display_lock:
            stats = self.routing_stats[test_id]
            total_requests = stats["total_requests"]
            variant_request_count = stats["variant_counts"][selected_variant]
        
        # Calculate actual routing percentage
        actual_percentage = (variant_request_count / total_requests * 100) if total_requests > 0 else 0
        target_percentage = allocations.get(selected_variant, 0) * 100
        
        # Create metrics summary
        metrics_summary = []
        if total_samples > 0:
            metrics_summary.append(f"rate={conversion_rate:.3f}")
            metrics_summary.append(f"samples={total_samples:,}")
        
        confidence = self._calculate_confidence(variant_metrics)
        if confidence > 0:
            metrics_summary.append(f"conf={confidence:.2f}")
        
        metrics_str = f"({', '.join(metrics_summary)})" if metrics_summary else "(no data)"
        
        # Format allocation comparison
        allocation_str = f"target={target_percentage:.1f}% actual={actual_percentage:.1f}%"
        
        # Print routing result
        print(f"[{timestamp}] [{test_id}] → {selected_variant} | {allocation_str} | {metrics_str}")
    
    def _display_summary_stats(self):
        """
        Display summary statistics for all tests (thread-safe).
        """
        if not self.display_results:
            return
        
        # Get thread-safe snapshots
        with self.display_lock:
            routing_stats_copy = {}
            for test_id, stats in self.routing_stats.items():
                routing_stats_copy[test_id] = {
                    "total_requests": stats["total_requests"],
                    "variant_counts": dict(stats["variant_counts"])
                }
        
        current_allocations_copy = self._get_current_allocations_snapshot()
        metrics_copy = self._get_current_metrics_snapshot()
        
        if not routing_stats_copy:
            return
        
        print("\n" + "="*80)
        print("ROUTING SUMMARY")
        print("="*80)
        print(f"Metrics Messages: {self.metrics_messages_processed:,} | Routing Decisions: {self.routing_decisions_made:,}")
        print("="*80)
        
        for test_id, stats in routing_stats_copy.items():
            total_requests = stats["total_requests"]
            variant_counts = stats["variant_counts"]
            
            if total_requests == 0:
                continue
            
            print(f"\nTest: {test_id} (Total Requests: {total_requests:,})")
            print("-" * 60)
            
            # Show current allocations
            current_allocations = current_allocations_copy.get(test_id, {})
            
            for variant in sorted(set(list(variant_counts.keys()) + list(current_allocations.keys()))):
                request_count = variant_counts.get(variant, 0)
                actual_pct = (request_count / total_requests * 100) if total_requests > 0 else 0
                target_pct = current_allocations.get(variant, 0) * 100
                
                # Get metrics
                variant_metrics = metrics_copy.get(test_id, {}).get(variant, {})
                alpha = variant_metrics.get("alpha", 1)
                beta_param = variant_metrics.get("beta", 1)
                conversion_rate = alpha / (alpha + beta_param) if (alpha + beta_param) > 0 else 0.0
                total_samples = alpha + beta_param - 2
                
                metrics_info = ""
                if total_samples > 0:
                    metrics_info = f" | Conv: {conversion_rate:.3f} | Samples: {total_samples:,}"
                
                print(f"  {variant:>12}: {request_count:>6} reqs ({actual_pct:>5.1f}%) | "
                      f"Target: {target_pct:>5.1f}%{metrics_info}")
        
        print("\n" + "="*80)
        print("Press ESC to stop routing simulation...")
        print("="*80 + "\n")
    
    def _check_for_escape_key(self) -> bool:
        """
        Check if escape key has been pressed (non-blocking).
        
        Returns:
            True if escape key was pressed, False otherwise
        """
        if sys.platform == "win32":
            import msvcrt
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if ord(key) == 27:  # ESC key
                    return True
        else:
            # Unix/Linux/Mac
            if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
                key = sys.stdin.read(1)
                if ord(key) == 27:  # ESC key
                    return True
        return False
    
    def _process_metrics_message(self, message: Dict[str, Any]):
        """Process incoming metrics message (thread-safe)."""
        test_id = message.get("test_id")
        variant = message.get("variant")
        
        if not test_id or not variant:
            return
        
        with self.data_lock:
            # Update metrics for this test/variant
            if "alpha" in message and "beta" in message:
                # Posterior update message
                self.test_metrics[test_id][variant] = {
                    "alpha": message["alpha"],
                    "beta": message["beta"],
                    "timestamp": message.get("timestamp", iso_now())
                }
                
                # Track performance history
                alpha = message["alpha"]
                beta_param = message["beta"]
                conversion_rate = alpha / (alpha + beta_param) if (alpha + beta_param) > 0 else 0.0
                self.performance_history[test_id][variant].append(conversion_rate)
                
                # Update last metrics timestamp
                self.last_metrics_update[test_id] = time.time()
                
                logging.debug("[ROUTER] [%s] Updated metrics for %s: α=%d β=%d rate=%.3f", 
                             test_id, variant, alpha, beta_param, conversion_rate)
            
            elif "success_rate_total" in message:
                # Progress metrics message
                existing_metrics = self.test_metrics[test_id].get(variant, {})
                existing_metrics.update({
                    "success_rate": message["success_rate_total"],
                    "exposures_total": message.get("exposures_total", 0),
                    "successes_total": message.get("successes_total", 0),
                    "timestamp": message.get("timestamp", iso_now())
                })
                self.test_metrics[test_id][variant] = existing_metrics
                
                # Update last metrics timestamp
                self.last_metrics_update[test_id] = time.time()
            
            self.metrics_messages_processed += 1
    
    def _kafka_consumer_loop(self):
        """
        Background thread that continuously reads metrics from Kafka.
        This runs independently of routing decisions to ensure real-time metric updates.
        """
        logging.info("[ROUTER] Starting Kafka consumer thread")
        
        while self.running:
            try:
                # Poll for messages with shorter timeout for responsiveness
                msg = self.consumer.poll(0.5)
                
                if msg is None:
                    continue
                
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        logging.error("[ROUTER] Kafka error: %s", msg.error())
                    continue
                
                # Process message
                try:
                    message = json.loads(msg.value().decode('utf-8'))
                    self._process_metrics_message(message)
                    
                    # Commit offset
                    self.consumer.commit(asynchronous=True)
                    
                except Exception as e:
                    logging.error("[ROUTER] Error processing Kafka message: %s", e)
                
            except Exception as e:
                logging.error("[ROUTER] Error in Kafka consumer loop: %s", e)
                if self.running:
                    time.sleep(1)  # Brief pause before retrying
        
        logging.info("[ROUTER] Kafka consumer thread stopped")
    
    def _routing_decision_loop(self):
        """
        Background thread that makes routing decisions based on current metrics.
        This runs independently of Kafka consumption.
        """
        logging.info("[ROUTER] Starting routing decision thread")
        
        while self.running:
            try:
                # Check if it's time to update routing decisions
                if self._should_update_routing():
                    self._update_routing_decisions()
                
                # Sleep for a short interval before checking again
                time.sleep(1.0)
                
            except Exception as e:
                logging.error("[ROUTER] Error in routing decision loop: %s", e)
                if self.running:
                    time.sleep(1)  # Brief pause before retrying
        
        logging.info("[ROUTER] Routing decision thread stopped")
    
    def _get_current_metrics_snapshot(self) -> Dict[str, Dict[str, Dict]]:
        """
        Get a thread-safe snapshot of current metrics for routing decisions.
        
        Returns:
            Deep copy of current test metrics
        """
        with self.data_lock:
            # Create a deep copy to avoid threading issues
            snapshot = {}
            for test_id, variants in self.test_metrics.items():
                snapshot[test_id] = {}
                for variant, metrics in variants.items():
                    snapshot[test_id][variant] = dict(metrics)  # Shallow copy of metrics dict
            return snapshot
    
    def _get_current_allocations_snapshot(self) -> Dict[str, Dict[str, float]]:
        """
        Get a thread-safe snapshot of current allocations for routing simulation.
        
        Returns:
            Deep copy of current allocations
        """
        with self.data_lock:
            # Create a deep copy to avoid threading issues
            snapshot = {}
            for test_id, allocations in self.current_allocations.items():
                snapshot[test_id] = dict(allocations)  # Shallow copy of allocations dict
            return snapshot

    def run(self):
        """Run the dynamic router main loop with separate Kafka and routing threads."""
        logging.info("[ROUTER] Starting dynamic router with algorithm: %s", self.algorithm)
        logging.info("[ROUTER] Update interval: %ds, Min samples: %d, Exploration rate: %.2f", 
                    self.update_interval, self.min_samples, self.exploration_rate)
        logging.info("[ROUTER] Separating Kafka consumption from routing decisions")
        
        # Start background threads
        try:
            # Start Kafka consumer thread
            self.kafka_thread = threading.Thread(target=self._kafka_consumer_loop, daemon=True)
            self.kafka_thread.start()
            logging.info("[ROUTER] Kafka consumer thread started")
            
            # Start routing decision thread  
            self.routing_thread = threading.Thread(target=self._routing_decision_loop, daemon=True)
            self.routing_thread.start()
            logging.info("[ROUTER] Routing decision thread started")
            
            # Start traffic simulation thread if enabled
            if self.simulate_traffic:
                self.simulation_thread = threading.Thread(target=self._traffic_simulation_loop, daemon=True)
                self.simulation_thread.start()
                logging.info("[ROUTER] Traffic simulation thread started")
            
            # Display header if results display is enabled
            if self.display_results:
                self._display_header()
            
            # Main thread handles display updates and user input
            try:
                while self.running:
                    # Check for escape key in display mode
                    if self.display_results and self._check_for_escape_key():
                        logging.info("[ROUTER] Escape key pressed, stopping...")
                        self.running = False
                        break
                    
                    # Update summary display periodically
                    if self.display_results and (time.time() - self.last_display_update) > 10:
                        self._display_summary_stats()
                        self.last_display_update = time.time()
                    
                    # Brief sleep to avoid busy waiting
                    time.sleep(0.1)
            
            except KeyboardInterrupt:
                logging.info("[ROUTER] Router interrupted by user")
                self.running = False
        
        finally:
            # Stop all threads gracefully
            logging.info("[ROUTER] Stopping all threads...")
            
            # Wait for threads to finish
            if self.kafka_thread and self.kafka_thread.is_alive():
                logging.info("[ROUTER] Waiting for Kafka consumer thread...")
                self.kafka_thread.join(timeout=3)
            
            if self.routing_thread and self.routing_thread.is_alive():
                logging.info("[ROUTER] Waiting for routing decision thread...")
                self.routing_thread.join(timeout=2)
            
            if self.simulate_traffic and hasattr(self, 'simulation_thread') and self.simulation_thread and self.simulation_thread.is_alive():
                logging.info("[ROUTER] Waiting for traffic simulation thread...")
                self.simulation_thread.join(timeout=2)
            
            # Display final summary if enabled
            if self.display_results:
                self._display_summary_stats()
            
            # Final statistics
            logging.info("[ROUTER] Metrics messages processed: %d", self.metrics_messages_processed)
            logging.info("[ROUTER] Routing decisions made: %d", self.routing_decisions_made)
            
            # Cleanup Kafka resources
            self.producer.flush(5)
            self.consumer.close()
            logging.info("[ROUTER] Dynamic router stopped")
    
    def _display_header(self):
        """
        Display the header for real-time routing results.
        """
        print("\n" + "="*100)
        print("DIANA DYNAMIC ROUTER - REAL-TIME ROUTING DECISIONS")
        print("="*100)
        print(f"Algorithm: {self.algorithm.title()} | Interval: {self.routing_request_interval}s | Min Samples: {self.min_samples}")
        print("-"*100)
        print("Format: [TIME] [TEST] → VARIANT | target=X% actual=Y% | (rate=Z.ZZZ, samples=N, conf=0.XX)")
        print("-"*100)
        print("Press ESC to stop...\n")


def main():
    """
    Main entry point for the dynamic router CLI.
    """
    parser = argparse.ArgumentParser(
        description="Dynamic traffic router based on A/B test metrics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start dynamic router with Thompson Sampling
  diana-route --config experiments.yaml --algorithm thompson_sampling
  
  # Use Epsilon-Greedy with custom parameters
  diana-route --config experiments.yaml --algorithm epsilon_greedy --exploration-rate 0.2
  
  # Fast updates with frequent routing requests
  diana-route --config experiments.yaml --update-interval 10 --routing-interval 2.0
  
  # Disable traffic simulation (metrics-only mode)
  diana-route --config experiments.yaml --no-simulate-traffic
  
  # Display real-time routing results (press ESC to stop)
  diana-route --config experiments.yaml --display
        """)
    
    parser.add_argument("--config", required=True,
                        help="path to experiments.yaml")
    parser.add_argument("--algorithm", choices=["thompson_sampling", "epsilon_greedy"],
                        default="thompson_sampling",
                        help="routing algorithm (default: thompson_sampling)")
    parser.add_argument("--simulate-traffic", action="store_true", default=True,
                        help="enable traffic simulation (default: enabled)")
    parser.add_argument("--no-simulate-traffic", action="store_true",
                        help="disable traffic simulation")
    parser.add_argument("--routing-interval", type=float, default=5.0,
                        help="routing request interval in seconds (default: 5.0)")
    parser.add_argument("--display", action="store_true",
                        help="display real-time routing results (press ESC to stop)")
    parser.add_argument("--update-interval", type=int, default=30,
                        help="routing update interval in seconds (default: 30)")
    parser.add_argument("--min-samples", type=int, default=100,
                        help="minimum samples per variant before routing (default: 100)")
    parser.add_argument("--exploration-rate", type=float, default=0.1,
                        help="exploration rate for algorithms (default: 0.1)")
    parser.add_argument("--confidence-threshold", type=float, default=0.95,
                        help="confidence threshold for decisions (default: 0.95)")
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
    
    # Create broker and routing configuration
    brokers = kafka_cfg.get("brokers", ["localhost:9092"])
    kafka_config = {"bootstrap.servers": ",".join(brokers)}
    
    # Handle traffic simulation flags
    simulate_traffic = args.simulate_traffic and not args.no_simulate_traffic
    
    routing_config = {
        "algorithm": args.algorithm,
        "update_interval_seconds": args.update_interval,
        "min_samples_per_variant": args.min_samples,
        "exploration_rate": args.exploration_rate,
        "confidence_threshold": args.confidence_threshold,
        "simulate_traffic": simulate_traffic,
        "routing_request_interval": args.routing_interval,
        "display_results": args.display,
    }
    
    topics = kafka_cfg.get("topics", {})
    
    # Create and run router
    try:
        router = DynamicRouter(
            kafka_config=kafka_config,
            topics=topics,
            routing_config=routing_config,
            tests_config=cfg
        )
        
        router.run()
        
    except Exception as e:
        logging.error(f"Router error: {e}", exc_info=True)
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())