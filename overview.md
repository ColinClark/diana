
# `ab_bayesian_engine.py` – Module‑by‑Module Overview
_Last updated: 2025-05-12_  

---

## 1. High‑Level Purpose
The engine ingests **web events** from Kafka, updates **Bayesian
posteriors** for each experiment variant in real time, and periodically
emits:

* **Posterior snapshots** (`alpha`, `beta`) for every test/variant  
* **Progress metrics** (incremental & cumulative exposure/success counts)

The destination (**sink**) is configurable per the DSL:

* `console` – log the snapshot  
* `dynamodb` – write a row to a DynamoDB table  
* `csv` – append to a local CSV file

---

## 2. Dependencies & Imports
| Group | Modules | Why it’s needed |
|-------|---------|-----------------|
| **Std‑lib** | `json`, `time`, `argparse`, `threading`, `logging`, `csv`, `os`, `datetime`, `pathlib`, `collections` | CLI parsing, timing loops, thread‑safe counters, CSV output. |
| **DSL parsing** | `yaml` | Reads `experiments.yaml`. |
| **Fast JSON** | `ujson` (falls back to `json`) | Faster encode for Kafka payloads. |
| **AWS SDK** | `boto3` | Writes to DynamoDB when `output_sink=dynamodb`. |
| **Kafka** | `confluent_kafka.Producer`, `confluent_kafka.Consumer`, `KafkaError` | High‑performance event streaming. |

---

## 3. Utility Helpers
### `iso_now()`
Returns an ISO‑8601 timestamp (`YYYY‑MM‑DDTHH:MM:SSZ`) in UTC for logs
and payloads.

### `load_config(path)`
Loads the DSL YAML into a Python `dict`.

### `to_json(obj)`
Serialises a Python object to **bytes** using `ujson` if available, else
falls back to `json`.

---

## 4. Sink Helper Classes
### `PosteriorStoreDynamo`
Thin wrapper around a DynamoDB table. Implements a single method
`put(test_id, variant, alpha, beta, ts)`.

### `PosteriorStoreCSV`
Appends the same data to a CSV file, creating the header on first run.

If `output_sink` is `console`, no helper is instantiated—snapshots are
just logged.

---

## 5. `BayesianEngine` Class (Core)
### `__init__(cfg, run_secs, tick)`
1. Reads Kafka broker list from the DSL and creates one **producer** and
   one **consumer** (subscribed to `raw_events_in`).
2. Chooses the **sink** (`dynamodb`, `csv`, or `console`) and prepares
   the appropriate store.
3. Indexes **Bayesian tests** (`algorithm.type == "bayesian"`) into
   `self.tests` for O(1) look‑ups.
4. Sets up nested `defaultdict` structures for:
   * Posterior parameters `alpha`, `beta`
   * Exposure & success counters (total and incremental)

### `_in_window(test, ts)`
Returns `True` if an event timestamp falls between a test’s
`start_time` and `end_time`.

### `run()`
Main loop until `run_secs` has elapsed:

1. `consumer.poll(1.0)` – fetch next Kafka message.  
2. On each event call `_process()`, then asynchronous
   `consumer.commit()`.  
3. Call `_maybe_tick()` every `tick` seconds.  
4. After the wall‑clock limit, **flush** the producer and exit.

### `_process(ev)`
* Rejects events for non‑Bayesian tests or outside the test window.  
* Updates counters:  
  * `Displayed` → exposure +1  
  * `Clicked`   → success +1 and `alpha` +1  
  * Recomputes `beta = 1 + exposures − successes`

All updates are protected by a `threading.Lock`.

### `_maybe_tick()`
Runs every `tick` seconds:

1. For each `test_id, variant` pair:
   * Pop incremental counts.  
   * Build **posterior snapshot** and send to Kafka.  
   * Persist via the chosen sink (`store.put()` or log).  
   * Build **progress metrics** payload and send to Kafka.
2. Non‑blocking `producer.poll(0)` flushes deliveries.

---

## 6. Thread‑Safety
Although the engine is single‑threaded today, the use of a global
`Lock` around state mutation allows easy expansion to multi‑threaded
consumption without race conditions.

---

## 7. CLI Entrypoint
```bash
python ab_bayesian_engine.py     --config experiments.yaml     --run-for 3600     --progress-interval 60
```
| Flag | Meaning |
|------|---------|
| `--config` | Path to the DSL YAML file. |
| `--run-for` | Wall‑clock seconds before auto‑shutdown. |
| `--progress-interval` | Seconds between progress snapshots. |

---

## 8. Extensibility Points
* **New sinks** – implement a class with `.put(...)` and add a branch in
  `__init__`.  
* **Additional likelihoods** – store extra parameters (e.g., μ/σ for
  Gaussian) and update the `_process()` method accordingly.  
* **Dynamic re‑allocation** – produce decisions to a Kafka topic and have
  the front‑end consume them.

---

## 9. Runtime Flow Diagram
```text
raw_events_in ─▶ Consumer ──▶ _process() ─┐
                                          └─► Update α/β & counters
                               ▲
     every <tick> seconds      │
                               ▼
                            _maybe_tick()
                               │
     ┌────────────┬──────────────────────┬───────────────┐
     │            │                      │               │
posteriors_out progress→metrics_out   sink (console│dynamo│csv)
```

---

*End of overview.*
