"""Tests for src.train module."""

import json
import os

import joblib
import pytest

from src.train import train


def test_train_creates_artifacts(tmp_path):
    model_out = str(tmp_path / "model.pkl")
    metrics_out = str(tmp_path / "metrics.json")

    metrics = train(model_out=model_out, metrics_out=metrics_out)

    # Files should exist
    assert os.path.isfile(model_out)
    assert os.path.isfile(metrics_out)

    # Model should be loadable
    pipeline = joblib.load(model_out)
    assert hasattr(pipeline, "predict")

    # Metrics JSON should be valid
    with open(metrics_out) as f:
        saved_metrics = json.load(f)
    assert "accuracy" in saved_metrics
    assert "f1_macro" in saved_metrics
    assert "n_train" in saved_metrics
    assert "n_test" in saved_metrics
    assert "classes" in saved_metrics

    # Accuracy should be at least 0.9
    assert saved_metrics["accuracy"] >= 0.9

    # Returned metrics match saved metrics
    assert metrics == saved_metrics
