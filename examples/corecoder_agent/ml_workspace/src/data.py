"""Data loading and splitting utilities for the Iris dataset."""

from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split
import numpy as np


def load_iris_dataset():
    """Load the Iris dataset.

    Returns:
        tuple: (X, y, feature_names, target_names)
    """
    bunch = load_iris()
    X = bunch.data
    y = bunch.target
    feature_names = list(bunch.feature_names)
    target_names = list(bunch.target_names)
    return X, y, feature_names, target_names


def split_train_test(X, y, test_size=0.2, random_state=42):
    """Split data into stratified train/test sets.

    Args:
        X: Feature matrix.
        y: Target vector.
        test_size: Fraction of data reserved for testing.
        random_state: Random seed for reproducibility.

    Returns:
        tuple: (X_train, X_test, y_train, y_test)
    """
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y
    )
    return X_train, X_test, y_train, y_test
