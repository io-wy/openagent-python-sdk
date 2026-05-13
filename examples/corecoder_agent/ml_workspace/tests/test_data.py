"""Tests for src.data module."""

import numpy as np
import pytest

from src.data import load_iris_dataset, split_train_test


def test_load_iris_dataset_shapes():
    X, y, feature_names, target_names = load_iris_dataset()
    assert X.shape == (150, 4)
    assert y.shape == (150,)
    assert len(feature_names) == 4
    assert len(target_names) == 3


def test_split_shapes_and_sizes():
    X, y, _, _ = load_iris_dataset()
    X_train, X_test, y_train, y_test = split_train_test(X, y)
    # 80/20 split of 150 samples
    assert X_train.shape[0] == y_train.shape[0]
    assert X_test.shape[0] == y_test.shape[0]
    assert X_train.shape[0] + X_test.shape[0] == 150
    assert X_train.shape[1] == 4
    assert X_test.shape[1] == 4


def test_stratified_split_label_coverage():
    X, y, _, target_names = load_iris_dataset()
    X_train, X_test, y_train, y_test = split_train_test(X, y)
    # All three classes should appear in both splits
    assert len(np.unique(y_train)) == 3
    assert len(np.unique(y_test)) == 3
