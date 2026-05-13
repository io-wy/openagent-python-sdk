# Task: End-to-End Iris Classification Microservice

Build a complete, runnable ML microservice in this workspace
(`examples/corecoder_agent/ml_workspace/`). All code, tests, artifacts, and
documentation live under this directory — do not touch anything outside it.

## Stack constraints

- Python only. No GPU.
- Dependencies: `scikit-learn`, `fastapi`, `uvicorn`, `joblib`, `pandas`,
  `numpy`, `pydantic`, `httpx`, `pytest`. **All of these are already installed
  in the project's `uv` virtualenv** — do **not** run `pip install` or modify
  `pyproject.toml`. If a tool says "module not found", verify with
  `uv run python -c "import X"` from the repo root before assuming you must
  install it.
- Use the `iris` dataset from `sklearn.datasets.load_iris()`. No network
  access required.

## Required pipeline

1. **Data layer** (`src/data.py`)
   - `load_iris_dataset()` returns `(X, y, feature_names, target_names)`.
   - `split_train_test(X, y, test_size=0.2, random_state=42)` returns
     `(X_train, X_test, y_train, y_test)`. Use stratified split.

2. **Training** (`src/train.py`)
   - CLI entry: `python -m src.train [--model-out PATH] [--metrics-out PATH]`.
   - Defaults: `artifacts/model.pkl`, `artifacts/metrics.json`.
   - Pipeline: `StandardScaler` + `LogisticRegression(max_iter=1000)`.
   - Save the trained pipeline with `joblib.dump`.
   - Save metrics JSON: `{"accuracy": ..., "f1_macro": ..., "n_train": ..., "n_test": ..., "classes": [...]}`.
   - Print a one-line summary to stdout.

3. **Evaluation** (`src/evaluate.py`)
   - CLI entry: `python -m src.evaluate [--model PATH]`.
   - Loads the model, recomputes metrics on the held-out split, prints a
     classification report.

4. **Inference API** (`src/api.py`)
   - FastAPI app exposed as `app`.
   - `GET /health` → `{"status": "ok", "model_loaded": bool}`.
   - `POST /predict` body: `{"features": [[f1,f2,f3,f4], ...]}` → response:
     `{"predictions": ["setosa", ...], "probabilities": [[..],..]}`.
   - Validate the input with Pydantic: each feature row must have exactly 4
     floats; reject otherwise with HTTP 422 (FastAPI does this automatically
     if you use the right model).
   - Loads the model lazily on first request from `artifacts/model.pkl` (or
     env var `MODEL_PATH` if set).

5. **Tests** (`tests/`)
   - `tests/test_data.py` — assert split shapes and label coverage.
   - `tests/test_train.py` — train into a `tmp_path`, assert files exist and
     accuracy ≥ 0.9.
   - `tests/test_api.py` — use `fastapi.testclient.TestClient`. After running
     training into `tmp_path` and pointing `MODEL_PATH` at it, hit `/health`
     and `/predict` with one valid sample and one malformed sample. Assert
     200 / 422 and prediction is one of the iris class names.

6. **Glue**
   - `README.md` (≤ 80 lines) — install / train / serve / test commands.
   - `Makefile` with targets: `train`, `test`, `serve`, `clean`. The `serve`
     target runs `uvicorn src.api:app --reload --port 8000`.

## Layout

Target structure (you may add files but must not skip these):

```
ml_workspace/
  README.md
  Makefile
  src/
    __init__.py
    data.py
    train.py
    evaluate.py
    api.py
  tests/
    __init__.py
    test_data.py
    test_train.py
    test_api.py
  artifacts/        # created by training; can be empty initially
```

## Acceptance criteria

You are done when **all** of the following pass, run from
`examples/corecoder_agent/ml_workspace/`:

```bash
uv run python -m src.train               # writes artifacts/model.pkl + metrics.json
uv run python -m src.evaluate            # prints a classification report
uv run python -m pytest -q               # all tests green
```

Verify each command yourself with the `bash` tool before declaring success.
Do not claim a step works without running it.

## Working rules

- Use `cd examples/corecoder_agent/ml_workspace` once at the start, then keep
  working with relative paths.
- Long commands (training, full test run): pass `timeout=300` to `bash`.
- Use `edit_file` for surgical changes once a file exists; only use
  `write_file` for the initial creation.
- If you hit a bug, fix it and re-run the failing command — don't move on
  with red tests.
- `sub_agent` is available; use it if you want to delegate (e.g. "write the
  test file for src/data.py") but it counts against your overall budget.

When everything passes, summarize:
1. The files you created.
2. The accuracy / f1 you achieved.
3. The commands you ran to verify.
