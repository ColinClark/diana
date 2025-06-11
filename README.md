# Bayesian A/B Engine Demo

> Real‑time multi‑arm bandit experimentation with Python, Kafka, and DynamoDB  
> _Last updated: 2025-05-14_

---

## Table of Contents
1. [What's in this repo?](#whats-in-this-repo)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Quick‑start demo](#quick-start-demo)
5. [Interpreting the output](#interpreting-the-output)
6. [Switching sinks](#switching-sinks)
7. [Cleaning up](#cleaning-up)
8. [Package Structure](#package-structure)
9. [FAQ](#faq)

---

## What's in this repo?

| Path | Purpose |
|------|---------|
| `diana/` | Core package directory with modular implementation |
| `docker-compose.yml` | Spins up **Kafka + ZooKeeper**, **DynamoDB‑Local**, and the **Bayesian engine** container. |
| `experiments.yaml` | DSL describing experiments, infra, and the chosen output sink (`console`, `dynamodb`, or `csv`). |
| `requirements.txt` | Python dependency pins. |
| `setup.py` | Package installation script. |

---

## Architecture

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
           │                                    │ real-time metrics
           │ sink = console | csv | dynamodb    ▼
           ▼                            ┌───────────────┐
   ┌──────────────┐                    │ diana-route   │
   │  CSV file    │  or  DynamoDB      │ (Thompson/ε)  │
   └──────────────┘      table         └───────┬───────┘
                                               │ routing decisions
                                               ▼
                                       ┌───────────────┐
                                       │ Load Balancer │
                                       │ Integration   │
                                       └───────────────┘
```

---

## Prerequisites

* **Docker + Docker Compose v2**  
* **Python 3.11** (if you want to run scripts outside the container)  
* Basic **GNU/Linux command‑line** tools

---

## Quick‑start demo

### 1. Clone and build

```bash
git clone https://github.com/your‑org/ab‑bayesian‑demo.git
cd ab‑bayesian‑demo
docker compose up --build -d   # first run may take a minute
```

> The compose file auto‑installs Python deps inside the `ab-engine`
> container and exposes Kafka at `localhost:9092`.

### 2. Generate traffic

```bash
# 50 exposures per second for 2 minutes
diana-traffic --config experiments.yaml --eps 50 --duration 120 --prob "control=0.35,treatment=0.55"
```

You'll see logs like:

```
12:00:05 Published to web-events  (brokers=kafka:9092)  for 120 s
12:00:25 Finished – sent 6 000 ButtonDisplayed events
```

### 3. Watch the engine

Tail the container:

```bash
docker compose logs -f ab-engine
```

Example snapshot when `output_sink: console`:

```
Posterior ► {'test_id': 'button-color-test', 'variant': 'control',
              'timestamp': '2025-05-12T10:00:01Z', 'alpha': 42, 'beta': 118}
```

### 4. Analyse results (CSV sink)

If `output_sink: csv`:

```bash
diana-analyze posteriors_demo.csv
```

Sample output:

```
=== button-color-test  (n_variants=2) ===
  control:   mean=0.2410   95% CI=(0.2142, 0.2694)   α= 51  β= 161
 treatment:  mean=0.3097   95% CI=(0.2824, 0.3380)   α= 66  β= 148
  • Probability of superiority:
    control vs treatment:  P(control > treatment) = 0.024   ⇒ likely winner: treatment
```

### 5. Analyse results (DynamoDB sink)

```bash
aws dynamodb scan --table-name ab_posteriors --endpoint-url http://localhost:8000 --projection-expression "test_id,variant,alpha,beta,timestamp"
```

---

## Interpreting the output

| Field | Meaning |
|-------|---------|
| `alpha`, `beta` | Parameters of the **Beta posterior** for conversion rate \(p\). |
| Mean \(\hat p\) | `alpha / (alpha + beta)` – point estimate of CTR. |
| 95 % CI | Beta quantiles (2.5 %, 97.5 %). Non‑overlapping → strong evidence. |
| `exposures_inc` / `successes_inc` | Events since last tick (default 60 s). |
| `exposures_total` / `successes_total` | Cumulative counts. |
| `success_rate_total` | Running CTR. |

**Probability of superiority** from `diana-analyze` answers  
"_How likely is Variant A better than Variant B right now?_"

---

## Switching sinks

Edit **`experiments.yaml`**:

```yaml
output_sink: console      # demo mode
# output_sink: csv
# output_sink: dynamodb
```

* **console** – easiest to inspect, nothing persists  
* **csv** – appends `posteriors_<date>.csv` (good for offline plots)  
* **dynamodb** – durable storage; ready for dashboards & Lambda alerts

---

## Cleaning up

```bash
docker compose down -v   # stops containers and removes volumes
rm posteriors_demo.csv   # if you used the CSV sink
```

---

## Package Structure

The codebase has been refactored into a proper Python package with the following structure:

```
diana/
  __init__.py            # Package metadata
  engine/
    __init__.py
    bayesian.py          # Core engine logic
    stores/
      __init__.py
      base.py            # Abstract store interface
      console.py         # Console output implementation
      csv.py             # CSV file implementation
      dynamodb.py        # DynamoDB implementation
  utils/
    __init__.py
    config.py            # Configuration utilities
    kafka.py             # Kafka client wrapper
  cli/
    __init__.py
    engine_cli.py        # Bayesian engine command-line interface
    analyze_cli.py       # Results analyzer command-line interface
    traffic_cli.py       # Traffic generator command-line interface
    route_cli.py         # Dynamic traffic routing command-line interface
    monitor_cli.py       # Real-time monitoring command-line interface
```

To install the package in development mode:

```bash
pip install -e .
```

This will make the following Diana CLI commands available:

```bash
# Run the Bayesian engine
diana-engine --config experiments.yaml --run-for 3600 --progress-interval 60

# Generate synthetic traffic
diana-traffic --config experiments.yaml --eps 50 --duration 120 --prob "control=0.35,treatment=0.55"

# Dynamic traffic routing with real-time optimization
diana-route --config experiments.yaml --display

# Analyze results
diana-analyze posteriors_demo.csv

# Monitor real-time Kafka messages
diana-monitor --config experiments.yaml

# Inject test events from a file (using shell script)
./inject_test_events.sh
```


---

## Dynamic Traffic Routing with diana-route

Diana includes a sophisticated **dynamic traffic routing system** (`diana-route`) that implements Multi-Armed Bandit algorithms to optimize traffic allocation in real-time based on Bayesian A/B test results.

### How diana-route Works

**diana-route** is a real-time routing optimizer that consumes Bayesian metrics from the engine and dynamically adjusts traffic allocation to maximize overall performance. It uses a **3-thread architecture** for optimal performance:

1. **Kafka Consumer Thread**: Continuously reads Bayesian posteriors (α, β parameters) from `posteriors_out` and `metrics_out` topics
2. **Routing Decision Thread**: Calculates optimal traffic allocations using Thompson Sampling or Epsilon-Greedy algorithms  
3. **Traffic Simulation Thread**: Simulates routing requests using current allocations and publishes decisions to Kafka

### Thompson Sampling Algorithm

The core routing algorithm works as follows:

1. **Posterior Sampling**: For each variant, sample conversion rates from Beta(α, β) distributions using real-time Bayesian parameters
2. **Win Probability**: Run 10,000 simulations to calculate probability each variant is optimal
3. **Traffic Allocation**: Allocate traffic proportional to win probabilities with exploration component
4. **Adaptation**: Update allocations every 30 seconds when significant changes occur (>5% threshold)

### Key Features

- **Real-time Bayesian Integration**: Uses live α/β parameters from the Bayesian engine to make optimal routing decisions
- **Thread-safe Architecture**: All shared data structures use locks to ensure consistent metrics access
- **Exploration vs Exploitation**: Balances exploitation of best-performing variants with exploration (default 10% exploration rate)
- **Adaptive Thresholds**: Only updates allocations when changes are significant enough to matter
- **Live Display**: Shows real-time routing decisions with performance metrics when using `--display` flag
- **Fallback Strategy**: Uses equal allocation when insufficient data is available (default: <100 samples per variant)

### Usage Examples

```bash
# Basic dynamic routing with Thompson Sampling
diana-route --config experiments.yaml

# Real-time display of routing decisions (press ESC to stop)
diana-route --config experiments.yaml --display

# Use Epsilon-Greedy algorithm with custom exploration rate
diana-route --config experiments.yaml --algorithm epsilon_greedy --exploration-rate 0.2

# Fast updates with frequent routing decisions
diana-route --config experiments.yaml --update-interval 10 --routing-interval 2.0

# Metrics-only mode (no traffic simulation)
diana-route --config experiments.yaml --no-simulate-traffic
```

### Display Output Format

When using `--display`, diana-route shows real-time routing decisions:

```
[14:23:15] [button-color-test] → treatment | target=67.3% actual=65.8% | (rate=0.412, samples=1,247, conf=0.85)
[14:23:20] [button-color-test] → control   | target=32.7% actual=34.2% | (rate=0.298, samples=891, conf=0.82)
```

#### Field Breakdown:

- **`[14:23:15]`** - Timestamp when this routing decision was made
- **`[button-color-test]`** - Test ID from experiments.yaml
- **`→ treatment`** - The variant selected for this simulated routing request (based on weighted random selection using current allocations)
- **`target=67.3%`** - The optimal allocation percentage calculated by Thompson Sampling algorithm based on Bayesian posteriors
- **`actual=65.8%`** - The actual percentage of requests routed to this variant so far in the simulation
- **`rate=0.412`** - Current conversion rate for this variant (calculated as α/(α+β) from Bayesian posteriors)
- **`samples=1,247`** - Total number of samples collected for this variant (α+β-2, subtracting the priors)
- **`conf=0.85`** - Confidence score (0-1) based on the width of the credible interval - narrower interval = higher confidence

#### What Each Line Represents:

Each line shows a **simulated routing request** where:
1. The algorithm determined treatment should get 67.3% of traffic (target allocation)
2. In practice, treatment has received 65.8% of simulated requests so far (actual allocation)
3. This variant has a 41.2% conversion rate based on 1,247 real samples from the Bayesian engine
4. The confidence in this conversion rate estimate is 85%

The display helps you monitor how well the Thompson Sampling algorithm is working and whether the actual traffic distribution matches the optimal allocation calculated from real-time Bayesian posteriors.

### Integration with Load Balancers

Diana-route publishes routing decisions to Kafka topics that can be consumed by:
- **Load balancers** (nginx, HAProxy, etc.) for real-time traffic splitting
- **API gateways** for dynamic route selection
- **CDNs** for edge-based A/B testing
- **Application code** for feature flag management

The routing decisions include current allocations and variant performance metrics, enabling external systems to implement optimized traffic distribution.

---

## FAQ

<details>
<summary>Q: Can I add more variants to the same test?</summary>

Yes! Add them under `tests[].variants` in **experiments.yaml** with an
initial allocation. The engine picks them up automatically on restart.
</details>

<details>
<summary>Q: How do I deploy this to production?</summary>

* Run Kafka and DynamoDB (or Aurora) on managed services.  
* Build the engine into a tiny container image (~60 MiB).  
* Use **Kubernetes deployments** with a single replica per test group or
scale out with partition‑key affinity.  
* Point `output_sink` to `dynamodb` and hook Grafana to the table.
</details>

<details>
<summary>Q: How do I add a new storage backend?</summary>

1. Create a new class in `diana/engine/stores/` that implements the `StoreManager` interface
2. Add your new store type to the `StoreManager.create()` factory method
3. Update the `experiments.yaml` file to use your new storage type
</details>

---

Happy testing 🎉