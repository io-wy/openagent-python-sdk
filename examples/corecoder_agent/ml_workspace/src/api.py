"""FastAPI inference API for the Iris classifier."""

import os
from typing import List

import joblib
import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel, field_validator

app = FastAPI(title="Iris Classifier API")

_model = None


def _get_model_path() -> str:
    return os.environ.get("MODEL_PATH", "artifacts/model.pkl")


def _load_model():
    global _model
    if _model is None:
        _model = joblib.load(_get_model_path())
    return _model


class PredictRequest(BaseModel):
    features: List[List[float]]

    @field_validator("features", mode="before")
    @classmethod
    def validate_features(cls, v):
        if not isinstance(v, list) or len(v) == 0:
            raise ValueError("features must be a non-empty list of rows")
        for row in v:
            if not isinstance(row, list) or len(row) != 4:
                raise ValueError("each feature row must have exactly 4 floats")
            for val in row:
                if not isinstance(val, (int, float)):
                    raise ValueError("each feature value must be a number")
        return v


class PredictResponse(BaseModel):
    predictions: List[str]
    probabilities: List[List[float]]


@app.get("/health")
def health():
    model_loaded = _model is not None
    try:
        _load_model()
        model_loaded = True
    except Exception:
        model_loaded = False
    return {"status": "ok", "model_loaded": model_loaded}


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    model = _load_model()
    X = np.array(req.features)
    preds = model.predict(X)
    probs = model.predict_proba(X)
    target_names = model.classes_
    # Map integer predictions to class names
    name_map = {i: name for i, name in enumerate(target_names)}
    pred_names = [str(name_map[int(p)]) for p in preds]
    return PredictResponse(
        predictions=pred_names,
        probabilities=probs.tolist(),
    )
