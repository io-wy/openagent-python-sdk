"""Tests for src.api module."""

import os

import pytest
from fastapi.testclient import TestClient

from src.train import train


@pytest.fixture()
def trained_model(tmp_path):
    """Train a model into tmp_path and return the path."""
    model_out = str(tmp_path / "model.pkl")
    metrics_out = str(tmp_path / "metrics.json")
    train(model_out=model_out, metrics_out=metrics_out)
    return model_out


@pytest.fixture()
def client(trained_model, monkeypatch):
    """Create a TestClient with MODEL_PATH pointing at the trained model."""
    monkeypatch.setenv("MODEL_PATH", trained_model)
    # Force module-level model reload
    import src.api as api_mod
    api_mod._model = None
    from src.api import app
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True


def test_predict_valid(client):
    # One valid Iris sample (setosa-like)
    resp = client.post("/predict", json={"features": [[5.1, 3.5, 1.4, 0.2]]})
    assert resp.status_code == 200
    body = resp.json()
    assert "predictions" in body
    assert "probabilities" in body
    assert len(body["predictions"]) == 1
    assert body["predictions"][0] in {"setosa", "versicolor", "virginica"}
    assert len(body["probabilities"]) == 1
    assert len(body["probabilities"][0]) == 3


def test_predict_malformed(client):
    # Wrong number of features — should get 422
    resp = client.post("/predict", json={"features": [[1.0, 2.0]]})
    assert resp.status_code == 422

    # Not a list of lists — should get 422
    resp = client.post("/predict", json={"features": "bad"})
    assert resp.status_code == 422
