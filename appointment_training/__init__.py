"""Offline data and evaluation utilities for the appointment model."""

from .dataset import DatasetManifest, DatasetValidationError, validate_dataset

__all__ = ["DatasetManifest", "DatasetValidationError", "validate_dataset"]
