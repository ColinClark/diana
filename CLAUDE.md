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
python traffic_generator.py --config experiments.yaml --eps 50 --duration 120 --prob "control=0.35,treatment=0.55"

# Watch the engine logs in real-time
docker compose logs -f ab-engine

# Analyze results (when using CSV sink)
python analyze_posteriors.py posteriors_demo.csv

# View results in DynamoDB (when using DynamoDB sink)
aws dynamodb scan --table-name ab_posteriors --endpoint-url http://localhost:8000 --projection-expression "test_id,variant,alpha,beta,timestamp"

# Clean up (stop containers and remove volumes)
docker compose down -v
```

### Development Workflow

```bash
# Run the Bayesian engine directly with custom parameters
python ab_bayesian_engine.py --config experiments.yaml --run-for 3600 --progress-interval 60

# Inject test events from a file
./inject_test_events.sh
```

## Architecture

### Core Components

1. **ab_bayesian_engine.py**: Real-time event processor that calculates Beta-Bernoulli posteriors.
   - Consumes events from Kafka
   - Updates Beta distribution parameters (alpha, beta)
   - Periodically emits posterior snapshots to configurable sinks

2. **traffic_generator.py**: Sends synthetic events to Kafka at a controllable rate.
   - Generates ButtonDisplayed and ButtonClicked events
   - Configurable events-per-second and success probabilities

3. **analyze_posteriors.py**: Reads posteriors data and prints metrics.
   - Calculates posterior means and credible intervals
   - Computes probability of superiority between variants

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
     └─────▲─────────┘                 └────────────────┘
           │
           │ sink = console | csv | dynamodb
           ▼
   ┌──────────────┐
   │  CSV file    │  or  DynamoDB table
   └──────────────┘
```

### Configuration

The system is configured via **experiments.yaml**, which defines:

- Infrastructure settings (Kafka brokers, topics, DynamoDB config)
- Output sink selection (console, CSV, or DynamoDB)
- Test definitions (variants, event types, success criteria)

## File Structure

- `docker-compose.yml`: Defines services (Kafka, ZooKeeper, DynamoDB-Local, Bayesian engine)
- `experiments.yaml`: Configuration for the Bayesian engine
- `ab_bayesian_engine.py`: Main engine implementation
- `traffic_generator.py`: Synthetic traffic generator
- `analyze_posteriors.py`: Utility for analyzing results
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