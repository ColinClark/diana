
# Bayesian A/B Engine Demo

> Real‑time multi‑arm bandit experimentation with Python, Kafka, and DynamoDB  
> _Last updated: 2025-05-12_

---

## Table of Contents
1. [What’s in this repo?](#whats-in-this-repo)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Quick‑start demo](#quick-start-demo)
5. [Interpreting the output](#interpreting-the-output)
6. [Switching sinks](#switching-sinks)
7. [Cleaning up](#cleaning-up)
8. [FAQ](#faq)

---

## What’s in this repo?

| Path | Purpose |
|------|---------|
| `docker-compose.yml` | Spins up **Kafka + ZooKeeper**, **DynamoDB‑Local**, and the **Bayesian engine** container. |
| `experiments.yaml` | DSL describing experiments, infra, and the chosen output sink (`console`, `dynamodb`, or `csv`). |
| `ab_bayesian_engine.py` | Real‑time event processor that calculates Beta‑Bernoulli posteriors. |
| `traffic_generator.py` | Sends synthetic `ButtonDisplayed` / `ButtonClicked` events to Kafka at a controllable rate. |
| `analyze_posteriors.py` | Reads the CSV sink (or DynamoDB export) and prints human‑friendly metrics. |
| `requirements.txt` | Python dependency pins. |

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
     └─────▲─────────┘                 └────────────────┘
           │
           │ sink = console | csv | dynamodb
           ▼
   ┌──────────────┐
   │  CSV file    │  or  DynamoDB table
   └──────────────┘
```

---

## Prerequisites

* **Docker + Docker Compose v2**  
* **Python 3.11** (if you want to run scripts outside the container)  
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
> container and exposes Kafka at `localhost:9092`.

### 2. Generate traffic

```bash
# 50 exposures per second for 2 minutes
python traffic_generator.py        --config experiments.yaml        --eps 50        --duration 120        --prob "control=0.35,treatment=0.55"
```

You’ll see logs like:

```
12:00:05 Published to web-events  (brokers=kafka:9092)  for 120 s
12:00:25 Finished – sent 6 000 ButtonDisplayed events
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
python analyze_posteriors.py posteriors_demo.csv
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
aws dynamodb scan     --table-name ab_posteriors     --endpoint-url http://localhost:8000     --projection-expression "test_id,variant,alpha,beta,timestamp"
```

---

## Interpreting the output

| Field | Meaning |
|-------|---------|
| `alpha`, `beta` | Parameters of the **Beta posterior** for conversion rate \(p\). |
| Mean \(\hat p\) | `alpha / (alpha + beta)` – point estimate of CTR. |
| 95 % CI | Beta quantiles (2.5 %, 97.5 %). Non‑overlapping → strong evidence. |
| `exposures_inc` / `successes_inc` | Events since last tick (default 60 s). |
| `exposures_total` / `successes_total` | Cumulative counts. |
| `success_rate_total` | Running CTR. |

**Probability of superiority** from `analyze_posteriors.py` answers  
“_How likely is Variant A better than Variant B right now?_”

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

## FAQ

<details>
<summary>Q: Can I add more variants to the same test?</summary>

Yes! Add them under `tests[].variants` in **experiments.yaml** with an
initial allocation. The engine picks them up automatically on restart.
</details>

<details>
<summary>Q: How do I deploy this to production?</summary>

* Run Kafka and DynamoDB (or Aurora) on managed services.  
* Build the engine into a tiny container image (~60 MiB).  
* Use **Kubernetes deployments** with a single replica per test group or
scale out with partition‑key affinity.  
* Point `output_sink` to `dynamodb` and hook Grafana to the table.
</details>

---

Happy testing 🎉
