"""
Model Manager — persistence, retraining, and validation pipeline.

Manages the lifecycle of ML models:
- Save/load for Isolation Forest and Autoencoder
- ONNX/TorchScript export for fast inference
- Weekly retraining schedule with validation
- Model versioning and promotion
- Model performance tracking
"""

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable
import numpy as np
import pandas as pd

from src.logging.structured_logger import get_logger

logger = get_logger(__name__)


class ModelManager:
    """
    Manages ML model lifecycle — training, saving, loading, versioning.
    
    Handles:
    - Model persistence (pickle, ONNX, TorchScript)
    - Training data preparation
    - Retraining scheduling
    - Model validation and promotion
    - Performance tracking
    """
    
    def __init__(self, models_dir: str = "./models", trained_dir: str = "./models/trained"):
        self.models_dir = Path(models_dir)
        self.trained_dir = Path(trained_dir)
        self.trained_dir.mkdir(parents=True, exist_ok=True)
        
        self._model_registry_file = self.models_dir / "model_registry.json"
        self._registry = self._load_registry()
        
        # Retraining state
        self._last_retrain: Optional[datetime] = None
        self._retrain_interval_hours: int = 24
        self._improvement_threshold: float = 0.05  # 5% improvement needed to promote
    
    # ── Registry Management ───────────────────────────────────────────────────
    
    def _load_registry(self) -> dict:
        """Load model registry from disk."""
        if self._model_registry_file.exists():
            try:
                with open(self._model_registry_file, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning("model_registry_corrupt", recreating=True)
        return {"models": {}, "version": 1}
    
    def _save_registry(self) -> None:
        """Save model registry to disk."""
        self._registry["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self._model_registry_file, 'w') as f:
            json.dump(self._registry, f, indent=2)
    
    def register_model(self, name: str, path: str, metrics: dict = None,
                       config_hash: str = None) -> str:
        """
        Register a model in the registry.
        
        Args:
            name: Model name (e.g., "isolation_forest", "autoencoder")
            path: File path to the saved model
            metrics: Performance metrics
            config_hash: Hash of config used for training
            
        Returns:
            Model version string.
        """
        version = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        entry = {
            "version": version,
            "path": str(path),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics or {},
            "config_hash": config_hash or "",
            "file_hash": self._hash_file(path) if os.path.exists(path) else "",
        }
        
        if name not in self._registry["models"]:
            self._registry["models"][name] = {"current": None, "versions": []}
        
        self._registry["models"][name]["versions"].append(entry)
        self._registry["models"][name]["current"] = version
        self._save_registry()
        
        logger.info("model_registered", name=name, version=version)
        return version
    
    def get_current_version(self, name: str) -> Optional[str]:
        """Get the current active version of a model."""
        return self._registry.get("models", {}).get(name, {}).get("current")
    
    def get_model_info(self, name: str, version: str = None) -> Optional[dict]:
        """Get information about a specific model version."""
        model_data = self._registry.get("models", {}).get(name, {})
        versions = model_data.get("versions", [])
        
        if not versions:
            return None
        
        if version is None:
            version = model_data.get("current")
        
        for entry in versions:
            if entry["version"] == version:
                return entry
        
        return None
    
    # ── Training Data Preparation ─────────────────────────────────────────────
    
    def prepare_training_sequences(self, df: pd.DataFrame, 
                                   sequence_length: int = 50,
                                   feature_columns: list[str] = None) -> np.ndarray:
        """
        Prepare sequences for autoencoder training from OHLCV data.
        
        Args:
            df: DataFrame with features
            sequence_length: Number of timesteps per sequence
            feature_columns: Columns to use (default: computed features)
            
        Returns:
            Numpy array of shape (n_sequences, sequence_length, n_features)
        """
        if df.empty:
            logger.warning("prepare_sequences_empty")
            return np.array([])
        
        if feature_columns is None:
            # Determine available feature columns
            feature_columns = [c for c in [
                'log_return', 'volatility_20', 'spread', 'tick_volume',
                'rsi', 'adx', 'zscore_200', 'volume_ratio'
            ] if c in df.columns]
        
        if not feature_columns:
            logger.error("prepare_sequences_no_features")
            return np.array([])
        
        # Extract and normalize features
        data = df[feature_columns].values
        data = np.nan_to_num(data, nan=0.0)
        
        # Normalize
        mean = data.mean(axis=0)
        std = data.std(axis=0) + 1e-8
        data_norm = (data - mean) / std
        
        # Create sequences
        sequences = []
        for i in range(len(data_norm) - sequence_length + 1):
            sequences.append(data_norm[i:i+sequence_length])
        
        return np.array(sequences) if sequences else np.array([])
    
    def prepare_isolation_forest_data(self, df: pd.DataFrame) -> np.ndarray:
        """
        Prepare feature vectors for Isolation Forest training.
        
        Args:
            df: DataFrame with features
            
        Returns:
            Numpy array of shape (n_samples, n_features)
        """
        feature_columns = [c for c in [
            'log_return', 'volatility_20', 'spread', 'tick_volume',
            'zscore_200', 'rsi', 'volume_ratio'
        ] if c in df.columns]
        
        if not feature_columns:
            return np.array([])
        
        data = df[feature_columns].fillna(0).values
        return data
    
    # ── Retraining Schedule ───────────────────────────────────────────────────
    
    def needs_retraining(self) -> bool:
        """Check if models need retraining based on schedule."""
        if self._last_retrain is None:
            return True
        
        next_retrain = self._last_retrain + timedelta(hours=self._retrain_interval_hours)
        return datetime.now(timezone.utc) >= next_retrain
    
    def mark_retrained(self) -> None:
        """Mark retraining as completed."""
        self._last_retrain = datetime.now(timezone.utc)
        logger.info("retraining_marked", time=self._last_retrain.isoformat())
    
    # ── Validation ────────────────────────────────────────────────────────────
    
    def validate_model(self, name: str, new_metrics: dict) -> bool:
        """
        Validate a newly trained model against current production.
        
        Args:
            name: Model name
            new_metrics: Dict of performance metrics
            
        Returns:
            True if new model should be promoted (better than current).
        """
        current_info = self.get_model_info(name)
        if current_info is None or not current_info.get("metrics"):
            return True  # No current model, promote new
        
        current_metrics = current_info["metrics"]
        
        # Compare key metrics (lower error is better)
        if "mse" in new_metrics and "mse" in current_metrics:
            improvement = (current_metrics["mse"] - new_metrics["mse"]) / current_metrics["mse"]
            if improvement > self._improvement_threshold:
                logger.info("model_improved",
                           model=name,
                           improvement=f"{improvement:.2%}",
                           old_mse=current_metrics["mse"],
                           new_mse=new_metrics["mse"])
                return True
            else:
                logger.info("model_not_improved",
                           model=name,
                           improvement=f"{improvement:.2%}")
                return False
        
        # For anomaly detection, compare F1 or precision
        if "f1_score" in new_metrics and "f1_score" in current_metrics:
            if new_metrics["f1_score"] > current_metrics["f1_score"]:
                return True
        
        return False
    
    # ── Utilities ─────────────────────────────────────────────────────────────
    
    @staticmethod
    def _hash_file(path: str) -> str:
        """Compute SHA-256 hash of a file."""
        if not os.path.exists(path):
            return ""
        sha = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha.update(chunk)
        return sha.hexdigest()[:16]
    
    @staticmethod
    def hash_config(config_dict: dict) -> str:
        """Compute hash of configuration for model provenance tracking."""
        config_str = json.dumps(config_dict, sort_keys=True, default=str)
        return hashlib.sha256(config_str.encode()).hexdigest()[:12]
    
    def get_status(self) -> dict:
        """Get model manager status."""
        return {
            "models_registered": list(self._registry.get("models", {}).keys()),
            "last_retrain": self._last_retrain.isoformat() if self._last_retrain else None,
            "needs_retraining": self.needs_retraining(),
        }


# ── Weekly Retraining Task ──────────────────────────────────────────────────

async def weekly_retrain(manager: ModelManager, 
                        data_provider: Callable,
                        anomaly_detector=None) -> dict:
    """
    Execute weekly model retraining pipeline.
    
    1. Fetch latest 3-6 months of historical data
    2. Prepare training sequences
    3. Retrain models
    4. Validate against current
    5. Promote if improved
    
    Args:
        manager: ModelManager instance
        data_provider: Async function that returns DataFrame
        anomaly_detector: AnomalyDetector instance for model retraining
        
    Returns:
        Dict with retraining results.
    """
    logger.info("weekly_retrain_starting")
    results = {}
    
    try:
        # 1. Fetch data
        df = await data_provider()
        
        if df is None or df.empty:
            logger.warning("weekly_retrain_no_data")
            return {"error": "no_data"}
        
        # 2. Prepare data
        if anomaly_detector and hasattr(anomaly_detector, 'layer3'):
            sequences = manager.prepare_training_sequences(df, sequence_length=50)
            
            if len(sequences) > 100:
                # 3. Train autoencoder
                train_history = anomaly_detector.layer3.train(
                    sequences, epochs=50, lr=1e-3
                )
                
                # 4. Compute validation metrics
                import torch
                test_data = torch.FloatTensor(sequences[-100:])
                with torch.no_grad():
                    recon, _ = anomaly_detector.layer3._model(test_data)
                    mse = torch.mean((test_data - recon) ** 2).item()
                
                new_metrics = {"mse": mse, "samples": len(sequences)}
                
                # 5. Validate and promote
                should_promote = manager.validate_model("autoencoder", new_metrics)
                
                if should_promote:
                    anomaly_detector.layer3.save(
                        str(manager.trained_dir / "autoencoder")
                    )
                    manager.register_model(
                        "autoencoder",
                        str(manager.trained_dir / "autoencoder.pt"),
                        metrics=new_metrics,
                    )
                    results["autoencoder"] = {"promoted": True, "mse": mse}
                else:
                    results["autoencoder"] = {"promoted": False, "mse": mse}
        
        # Isolation Forest retraining (simpler)
        if anomaly_detector and hasattr(anomaly_detector, 'layer2'):
            if_data = manager.prepare_isolation_forest_data(df)
            
            if len(if_data) > 100:
                from sklearn.ensemble import IsolationForest
                model = IsolationForest(
                    n_estimators=100, contamination=0.01, random_state=42
                )
                model.fit(if_data)
                
                # Score
                scores = model.decision_function(if_data)
                avg_score = float(np.mean(scores))
                anomaly_rate = float(np.mean(model.predict(if_data) == -1))
                
                new_metrics_if = {
                    "avg_score": avg_score,
                    "anomaly_rate": anomaly_rate,
                    "samples": len(if_data),
                }
                
                # Save
                import pickle
                if_path = manager.trained_dir / "isolation_forest.pkl"
                with open(if_path, 'wb') as f:
                    pickle.dump(model, f)
                
                manager.register_model(
                    "isolation_forest",
                    str(if_path),
                    metrics=new_metrics_if,
                )
                results["isolation_forest"] = {"saved": True, "anomaly_rate": anomaly_rate}
        
        manager.mark_retrained()
        logger.info("weekly_retrain_complete", results=results)
        
    except Exception as e:
        logger.error("weekly_retrain_error", error=str(e))
        results["error"] = str(e)
    
    return results
