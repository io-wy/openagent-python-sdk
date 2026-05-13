"""Train a LogisticRegression pipeline on the Iris dataset."""

import argparse
import json
import os
import sys

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.data import load_iris_dataset, split_train_test


def train(model_out: str, metrics_out: str) -> dict:
    """Train the model and save artifacts.

    Args:
        model_out: Path to save the trained pipeline (joblib).
        metrics_out: Path to save the metrics JSON.

    Returns:
        dict: The metrics dictionary.
    """
    X, y, feature_names, target_names = load_iris_dataset()
    X_train, X_test, y_train, y_test = split_train_test(X, y)

    pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=1000)),
    ])
    pipeline.fit(X_train, y_train)

    y_pred = pipeline.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    f1_macro = f1_score(y_test, y_pred, average="macro")

    metrics = {
        "accuracy": round(float(accuracy), 6),
        "f1_macro": round(float(f1_macro), 6),
        "n_train": int(X_train.shape[0]),
        "n_test": int(X_test.shape[0]),
        "classes": [str(name) for name in target_names],
    }

    os.makedirs(os.path.dirname(model_out) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(metrics_out) or ".", exist_ok=True)

    joblib.dump(pipeline, model_out)
    with open(metrics_out, "w") as f:
        json.dump(metrics, f, indent=2)

    summary = (
        f"Training complete — accuracy={metrics['accuracy']:.4f}, "
        f"f1_macro={metrics['f1_macro']:.4f}, "
        f"n_train={metrics['n_train']}, n_test={metrics['n_test']}"
    )
    print(summary)

    return metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description="Train Iris classifier")
    parser.add_argument("--model-out", default="artifacts/model.pkl",
                        help="Path to save the trained model pipeline")
    parser.add_argument("--metrics-out", default="artifacts/metrics.json",
                        help="Path to save the metrics JSON")
    args = parser.parse_args(argv)
    train(args.model_out, args.metrics_out)


if __name__ == "__main__":
    main()
