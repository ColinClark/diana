# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Diana is a Bayesian A/B testing engine demo that implements real-time multi-arm bandit experimentation using Python, Kafka, and DynamoDB. The system processes web events (like button displays and clicks), calculates Beta-Bernoulli posteriors, and outputs results to configurable sinks (console, CSV, or DynamoDB).

## Commands

### Running the System

```bash
# Start all services using Docker Compose
docker compose up --build -d

# Generate synthetic traffic (50 events per second for 2 minutes)
diana-traffic --config experiments.yaml --eps 50 --duration 120 --prob "control=0.35,treatment=0.55"

# Watch the engine logs in real-time
docker compose logs -f ab-engine

# Analyze results (when using CSV sink)
diana-analyze posteriors_demo.csv

# Enhanced analysis with plots and summary stats
diana-analyze posteriors_demo.csv --summary --plot

# Export detailed analysis to file
diana-analyze posteriors_demo.csv --export analysis_results.csv

# Monitor real-time Kafka messages from the engine
diana-monitor --config experiments.yaml

# Start dynamic traffic router with Thompson Sampling (default, with traffic simulation)
# Now uses separate threads: Kafka consumer + routing decisions + traffic simulation
diana-route --config experiments.yaml

# Display real-time routing results (press ESC to stop)
diana-route --config experiments.yaml --display

# Custom routing interval with live display
diana-route --config experiments.yaml --routing-interval 2.0 --display

# Epsilon-Greedy with custom exploration rate
diana-route --config experiments.yaml --algorithm epsilon_greedy --exploration-rate 0.2

# Metrics-only mode (no traffic simulation)
diana-route --config experiments.yaml --no-simulate-traffic

# View results in DynamoDB (when using DynamoDB sink)
aws dynamodb scan --table-name ab_posteriors --endpoint-url http://localhost:8000 --projection-expression "test_id,variant,alpha,beta,timestamp"

# Clean up (stop containers and remove volumes)
docker compose down -v
```

### Development Workflow

```bash
# Run the Bayesian engine directly with custom parameters
diana-engine --config experiments.yaml --run-for 3600 --progress-interval 60

# Inject test events from a file
./inject_test_events.sh
```

## Architecture

### Core Components

1. **diana-engine**: Real-time event processor that calculates Beta-Bernoulli posteriors.
   - Consumes events from Kafka
   - Updates Beta distribution parameters (alpha, beta)
   - Periodically emits posterior snapshots to configurable sinks

2. **diana-traffic**: Sends synthetic events to Kafka at a controllable rate.
   - Generates ButtonDisplayed and ButtonClicked events
   - Configurable events-per-second and success probabilities

3. **diana-analyze**: Comprehensive analysis of posterior data and test results.
   - Calculates posterior means and credible intervals
   - Computes probability of superiority between variants
   - Generates visualization plots (conversion rate evolution, posterior distributions)
   - Provides summary statistics and data quality metrics
   - Exports detailed analysis to CSV or JSON formats

4. **diana-monitor**: Real-time Kafka topic monitor for engine output.
   - Shows live posterior updates and metrics
   - Displays health information and processing stats
   - Formatted output for easy monitoring

5. **diana-route**: Dynamic traffic routing with built-in traffic simulation.
   - **Architecture**: Three separate threads for optimal performance:
     * Kafka Consumer Thread: Continuously reads metrics and updates shared state
     * Routing Decision Thread: Calculates optimal allocations using current metrics
     * Traffic Simulation Thread: Simulates routing requests using current allocations
   - Uses Thompson Sampling (default) or Epsilon-Greedy algorithms
   - Automatically adjusts traffic allocation based on real-time metrics
   - Simulates routing requests (configurable interval, default: 5 seconds)
   - Real-time display of routing decisions with performance metrics
   - Interactive mode with ESC key to stop (--display flag)
   - Thread-safe data structures ensure consistent metrics access
   - Publishes routing decisions and simulated requests to Kafka

### Data Flow

```
                        ┌───────────────┐
                        │ traffic_gen   │
                        │ (Python)      │
                        └───▲───────────┘
                            │ web‑events
                            ▼
     ┌───────────────┐   Kafka topic   ┌────────────────┐
     │  ab_engine    │ ──────────────▶ │ posteriors_out │
     │ (Python)      │                 │ metrics_out    │
     └─────▲─────────┘                 └────────┬───────┘
           │                                    │
           │ sink = console | csv | dynamodb    │ real-time metrics
           ▼                                    ▼
   ┌──────────────┐                    ┌───────────────┐
   │  CSV file    │  or  DynamoDB      │ diana-route   │
   │              │      table         │ (Thompson/ε)  │
   └──────────────┘                    └───────┬───────┘
                                               │ routing decisions
                                               ▼
                                       ┌───────────────┐
                                       │ Load Balancer │
                                       │ Integration   │
                                       └───────────────┘
```

### Configuration

The system is configured via **experiments.yaml**, which defines:

- Infrastructure settings (Kafka brokers, topics, DynamoDB config)
- Output sink selection (console, CSV, or DynamoDB)
- Test definitions (variants, event types, success criteria)

## File Structure

- `docker-compose.yml`: Defines services (Kafka, ZooKeeper, DynamoDB-Local, Bayesian engine)
- `experiments.yaml`: Configuration for the Bayesian engine
- `diana/`: Python package containing all core functionality
  - `diana/cli/engine_cli.py`: Main engine implementation (diana-engine command)
  - `diana/cli/traffic_cli.py`: Synthetic traffic generator (diana-traffic command)
  - `diana/cli/analyze_cli.py`: Utility for analyzing results (diana-analyze command)
  - `diana/engine/bayesian.py`: Core Bayesian engine logic
  - `diana/engine/stores/`: Output sink implementations (console, CSV, DynamoDB)
- `inject_test_events.sh`: Helper script to inject test events

## Dependencies

Major dependencies include:
- Python 3.11
- confluent_kafka (for Kafka interaction)
- boto3 (for DynamoDB interaction)
- scipy (for Beta distribution calculations)
- pandas (for data analysis)
- Docker and Docker Compose (for running the stack)

All Python dependencies are listed in requirements.txt.