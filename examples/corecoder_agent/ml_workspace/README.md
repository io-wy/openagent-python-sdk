# Iris Classification Microservice

End-to-end ML pipeline: data loading, training, evaluation, and a FastAPI
inference API — all using the classic Iris dataset.

## Prerequisites

All dependencies are available in the project `uv` virtualenv:

```
scikit-learn, fastapi, uvicorn, joblib, pandas, numpy, pydantic, httpx, pytest
```

No extra installs needed.

## Quick Start

```bash
cd examples/corecoder_agent/ml_workspace
```

### Train

```bash
uv run python -m src.train
# Writes artifacts/model.pkl and artifacts/metrics.json
```

### Evaluate

```bash
uv run python -m src.evaluate
# Prints a classification report to stdout
```

### Serve

```bash
uv run uvicorn src.api:app --reload --port 8000
```

Then:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"features": [[5.1, 3.5, 1.4, 0.2]]}'
```

### Test

```bash
uv run python -m pytest -q
```

### Make targets

| Target  | Command                              |
|---------|--------------------------------------|
| `train` | Train model → `artifacts/`           |
| `test`  | Run pytest                           |
| `serve` | Start uvicorn on port 8000           |
| `clean` | Remove saved artifacts               |

## Project layout

```
ml_workspace/
  src/            # data.py, train.py, evaluate.py, api.py
  tests/          # test_data.py, test_train.py, test_api.py
  artifacts/      # model.pkl + metrics.json (created by training)
  Makefile
```
