"""Evaluate a trained Iris classifier on the held-out test split."""

import argparse
import os

import joblib
from sklearn.metrics import classification_report

from src.data import load_iris_dataset, split_train_test


def evaluate(model_path: str) -> None:
    """Load model, recompute metrics on the test split, and print a report.

    Args:
        model_path: Path to the joblib-saved pipeline.
    """
    pipeline = joblib.load(model_path)

    X, y, feature_names, target_names = load_iris_dataset()
    X_train, X_test, y_train, y_test = split_train_test(X, y)

    y_pred = pipeline.predict(X_test)
    report = classification_report(y_test, y_pred, target_names=target_names)
    print(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate Iris classifier")
    parser.add_argument("--model", default=os.environ.get("MODEL_PATH", "artifacts/model.pkl"),
                        help="Path to the trained model pipeline")
    args = parser.parse_args(argv)
    evaluate(args.model)


if __name__ == "__main__":
    main()
