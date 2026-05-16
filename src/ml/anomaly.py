"""
Multi-Layer Anomaly Detection Engine.

Three detection layers running in ensemble:
1. Statistical Layer (fast): Z-score, volatility spike, price gap, spread widening
2. Isolation Forest (scikit-learn): Lightweight tree-based anomaly detection
3. Autoencoder (PyTorch): Sequence reconstruction for complex anomalies

Final anomaly flag = Statistical OR IsolationForest OR Autoencoder triggers.

Action on anomaly:
- Block new entries immediately
- Send CRITICAL Telegram alert
- Cooldown period (configurable 15-45 min)
- Log full feature snapshot with model scores
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional, Tuple
import pickle
from pathlib import Path
import warnings

from src.logging.structured_logger import get_logger

warnings.filterwarnings("ignore", category=UserWarning)

logger = get_logger(__name__)


# ── Data Types ──────────────────────────────────────────────────────────────

@dataclass
class AnomalyResult:
    """Result from anomaly detection check."""
    is_anomaly: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    layer_scores: dict = field(default_factory=dict)
    feature_snapshot: dict = field(default_factory=dict)
    trigger_reason: str = ""
    confidence: float = 0.0


# ── Layer 1: Statistical Anomaly Detection ──────────────────────────────────

class StatisticalAnomalyDetector:
    """
    Fast statistical anomaly detection using:
    - Z-score on log returns
    - Volatility spike detection
    - Price gap > threshold
    - Spread widening beyond percentile
    """
    
    def __init__(self, zscore_window: int = 200, zscore_threshold: float = 3.5,
                 volatility_spike_mult: float = 3.0, price_gap_atr_mult: float = 2.5,
                 spread_percentile: float = 99.0):
        self.zscore_window = zscore_window
        self.zscore_threshold = zscore_threshold
        self.volatility_spike_mult = volatility_spike_mult
        self.price_gap_atr_mult = price_gap_atr_mult
        self.spread_percentile = spread_percentile
        
        self._return_history: list = []
        self._volatility_history: list = []
        self._spread_history: list = []
    
    def update(self, price: float, spread: float, atr: float, timestamp: datetime) -> AnomalyResult:
        """
        Check current conditions for statistical anomalies.
        
        Args:
            price: Current mid price
            spread: Current bid-ask spread
            atr: Current ATR value
            timestamp: Current timestamp
            
        Returns:
            AnomalyResult with anomaly flag and trigger reasons.
        """
        reasons = []
        scores = {}
        
        # 1. Z-score check
        if len(self._return_history) > 1:
            prev_price = self._return_history[-1]['price'] if self._return_history else price
            if prev_price > 0:
                log_ret = np.log(price / prev_price)
                self._return_history.append({'price': price, 'return': log_ret})
            else:
                self._return_history.append({'price': price, 'return': 0.0})
        else:
            self._return_history.append({'price': price, 'return': 0.0})
        
        # Trim history
        while len(self._return_history) > self.zscore_window + 10:
            self._return_history.pop(0)
        
        if len(self._return_history) >= self.zscore_window // 2:
            returns = [r['return'] for r in self._return_history]
            recent = returns[-20:] if len(returns) >= 20 else returns
            all_returns = returns[-self.zscore_window:]
            
            mu = np.mean(all_returns)
            sigma = np.std(all_returns)
            
            if sigma > 0:
                current_zscore = abs(np.mean(recent)) / sigma
                scores['zscore'] = float(current_zscore)
                
                if current_zscore > self.zscore_threshold:
                    reasons.append(f"zscore_exceeded:{current_zscore:.2f}>{self.zscore_threshold}")
        
        # 2. Volatility spike
        if atr > 0:
            self._volatility_history.append(atr)
            while len(self._volatility_history) > 100:
                self._volatility_history.pop(0)
            
            if len(self._volatility_history) >= 20:
                median_vol = np.median(self._volatility_history)
                if median_vol > 0:
                    spike_ratio = atr / median_vol
                    scores['vol_spike_ratio'] = float(spike_ratio)
                    
                    if spike_ratio > self.volatility_spike_mult:
                        reasons.append(f"vol_spike:{spike_ratio:.2f}x")
        
        # 3. Spread widening
        if spread > 0:
            self._spread_history.append(spread)
            while len(self._spread_history) > 100:
                self._spread_history.pop(0)
            
            if len(self._spread_history) >= 20:
                percentile_val = np.percentile(self._spread_history, self.spread_percentile)
                scores['spread_pct'] = float((spread > self._spread_history).mean() * 100)
                
                if spread > percentile_val:
                    reasons.append(f"spread_widened:{spread:.5f}>{percentile_val:.5f}")
        
        is_anomaly = len(reasons) > 0
        return AnomalyResult(
            is_anomaly=is_anomaly,
            layer_scores=scores,
            trigger_reason=" | ".join(reasons) if reasons else "",
            confidence=min(len(reasons) * 0.4, 1.0)
        )
    
    def check_price_gap(self, current_mid: float, previous_mid: float,
                        atr: float) -> Tuple[bool, str]:
        """
        Check for sudden price gap.
        
        Args:
            current_mid: Current mid price
            previous_mid: Previous mid price
            atr: Current ATR value
            
        Returns:
            Tuple of (is_gap, description).
        """
        if atr <= 0:
            return False, ""
        
        gap = abs(current_mid - previous_mid)
        if gap > atr * self.price_gap_atr_mult:
            return True, f"price_gap:{gap:.4f}>({self.price_gap_atr_mult}*ATR={atr:.4f})"
        
        return False, ""


# ── Layer 2: Isolation Forest ───────────────────────────────────────────────

class IsolationForestDetector:
    """
    Lightweight Isolation Forest for anomaly detection.
    
    Features used: returns, volatility, spread, volume, price z-score.
    Retrained every N bars.
    """
    
    def __init__(self, n_estimators: int = 100, contamination: float = 0.01,
                 retrain_interval_bars: int = 500):
        self.n_estimators = n_estimators
        self.contamination = contamination
        self.retrain_interval_bars = retrain_interval_bars
        
        self._model = None
        self._feature_buffer: list = []
        self._bar_count = 0
        self._trained = False
        self._model_path = None
    
    def _get_model(self):
        """Lazy-load the Isolation Forest model."""
        if self._model is None:
            from sklearn.ensemble import IsolationForest
            self._model = IsolationForest(
                n_estimators=self.n_estimators,
                contamination=self.contamination,
                random_state=42,
                n_jobs=-1
            )
        return self._model
    
    def update(self, features: dict) -> AnomalyResult:
        """
        Check current features for anomalies using Isolation Forest.
        
        Args:
            features: Dict of feature values (returns, vol, spread, volume, zscore)
            
        Returns:
            AnomalyResult.
        """
        feature_vector = [
            features.get('log_return', 0.0),
            features.get('volatility', 0.0),
            features.get('spread', 0.0),
            features.get('tick_volume', 0.0),
            features.get('zscore', 0.0),
            features.get('rsi', 50.0) / 100.0,  # normalize
            features.get('volume_ratio', 1.0),
        ]
        
        self._feature_buffer.append(feature_vector)
        self._bar_count += 1
        
        # Trim buffer
        while len(self._feature_buffer) > self.retrain_interval_bars * 2:
            self._feature_buffer.pop(0)
        
        # Retrain if needed
        if self._bar_count % self.retrain_interval_bars == 0 and len(self._feature_buffer) >= 100:
            self._retrain()
        
        # Predict
        if not self._trained or len(self._feature_buffer) < 50:
            return AnomalyResult(is_anomaly=False, layer_scores={"if_score": 0.0})
        
        try:
            X = np.array([feature_vector])
            score = float(self._model.decision_function(X)[0])
            prediction = int(self._model.predict(X)[0])
            
            # -1 = anomaly, 1 = normal
            is_anomaly = prediction == -1
            
            return AnomalyResult(
                is_anomaly=is_anomaly,
                layer_scores={"if_score": score, "if_prediction": prediction},
                trigger_reason="isolation_forest" if is_anomaly else "",
                confidence=abs(score) / 0.5 if is_anomaly else 0.0
            )
        except Exception as e:
            logger.error("isolation_forest_predict_error", error=str(e))
            return AnomalyResult(is_anomaly=False)
    
    def _retrain(self) -> None:
        """Retrain the Isolation Forest model on recent feature buffer."""
        if len(self._feature_buffer) < 100:
            return
        
        try:
            X = np.array(self._feature_buffer[-min(len(self._feature_buffer), 1000):])
            self._get_model().fit(X)
            self._trained = True
            logger.info("isolation_forest_retrained", samples=len(X))
        except Exception as e:
            logger.error("isolation_forest_retrain_error", error=str(e))
    
    def save(self, path: str) -> None:
        """Save model to disk."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as f:
            pickle.dump({
                'model': self._model,
                'trained': self._trained,
                'bar_count': self._bar_count,
            }, f)
        logger.info("isolation_forest_saved", path=str(path))
    
    def load(self, path: str) -> bool:
        """Load model from disk."""
        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
            self._model = data['model']
            self._trained = data.get('trained', True)
            self._bar_count = data.get('bar_count', 0)
            logger.info("isolation_forest_loaded", path=path)
            return True
        except FileNotFoundError:
            logger.warning("isolation_forest_not_found", path=path)
            return False
        except Exception as e:
            logger.error("isolation_forest_load_error", error=str(e))
            return False


# ── Layer 3: LSTM Autoencoder ───────────────────────────────────────────────

class AutoencoderDetector:
    """
    Sequence-based anomaly detection using LSTM Autoencoder.
    
    Reconstructs sequences of normalized features. High reconstruction
    error indicates anomalous market conditions.
    
    Exported to ONNX or TorchScript for inference speed.
    """
    
    def __init__(self, sequence_length: int = 50, hidden_dim: int = 32,
                 latent_dim: int = 8, error_threshold_percentile: float = 95.0,
                 model_format: str = "onnx"):
        self.sequence_length = sequence_length
        self.hidden_dim = hidden_dim
        self.latent_dim = latent_dim
        self.error_threshold_percentile = error_threshold_percentile
        self.model_format = model_format
        
        self._model = None
        self._trained = False
        self._error_threshold = float('inf')
        self._error_history: list = []
        self._sequence_buffer: list = []
        self._feature_dim = 7  # return, vol, spread, volume, rsi, adx, zscore
        
        self._error_history_file: Optional[Path] = None
    
    def _build_model(self):
        """Build the LSTM Autoencoder PyTorch model."""
        import torch
        import torch.nn as nn
        
        class LSTMAutoencoder(nn.Module):
            def __init__(self, input_dim, hidden_dim, latent_dim, seq_len):
                super().__init__()
                self.seq_len = seq_len
                
                # Encoder
                self.encoder_lstm = nn.LSTM(
                    input_dim, hidden_dim, batch_first=True, num_layers=2, dropout=0.1
                )
                self.encoder_fc = nn.Linear(hidden_dim, latent_dim)
                
                # Decoder
                self.decoder_fc = nn.Linear(latent_dim, hidden_dim)
                self.decoder_lstm = nn.LSTM(
                    hidden_dim, input_dim, batch_first=True, num_layers=2, dropout=0.1
                )
            
            def forward(self, x):
                # x: (batch, seq_len, input_dim)
                enc_out, (h_n, _) = self.encoder_lstm(x)
                latent = self.encoder_fc(enc_out[:, -1, :])
                
                latent_expanded = latent.unsqueeze(1).repeat(1, self.seq_len, 1)
                dec_in = self.decoder_fc(latent_expanded)
                dec_out, _ = self.decoder_lstm(dec_in)
                
                return dec_out, latent
        
        return LSTMAutoencoder(
            input_dim=self._feature_dim,
            hidden_dim=self.hidden_dim,
            latent_dim=self.latent_dim,
            seq_len=self.sequence_length
        )
    
    def update(self, features: dict) -> AnomalyResult:
        """
        Check features for anomalies using autoencoder reconstruction error.
        
        Args:
            features: Dict of current feature values
            
        Returns:
            AnomalyResult.
        """
        feature_vector = [
            features.get('log_return', 0.0),
            features.get('volatility', 0.0),
            features.get('spread', 0.0),
            features.get('tick_volume', 0.0),
            features.get('rsi', 50.0) / 100.0,
            features.get('adx', 25.0) / 100.0,
            features.get('zscore', 0.0),
        ]
        
        self._sequence_buffer.append(feature_vector)
        while len(self._sequence_buffer) > self.sequence_length:
            self._sequence_buffer.pop(0)
        
        # Need full sequence for inference
        if len(self._sequence_buffer) < self.sequence_length or not self._trained:
            return AnomalyResult(is_anomaly=False, layer_scores={"ae_error": 0.0})
        
        try:
            error = self._compute_reconstruction_error()
            self._error_history.append(error)
            
            # Keep last 1000 errors
            while len(self._error_history) > 1000:
                self._error_history.pop(0)
            
            # Update threshold dynamically
            if len(self._error_history) >= 20:
                self._error_threshold = np.percentile(
                    self._error_history, self.error_threshold_percentile
                )
            
            is_anomaly = error > self._error_threshold and self._error_threshold > 0
            
            return AnomalyResult(
                is_anomaly=is_anomaly,
                layer_scores={
                    "ae_error": float(error),
                    "ae_threshold": float(self._error_threshold),
                },
                trigger_reason=f"autoencoder_error:{error:.4f}" if is_anomaly else "",
                confidence=min(float(error) / (self._error_threshold + 1e-8) - 0.5, 1.0)
            )
        except Exception as e:
            logger.error("autoencoder_predict_error", error=str(e))
            return AnomalyResult(is_anomaly=False)
    
    def _compute_reconstruction_error(self) -> float:
        """Compute MSE reconstruction error for current sequence."""
        import torch
        
        seq = np.array(self._sequence_buffer[-self.sequence_length:])
        
        # Normalize
        mean = seq.mean(axis=0)
        std = seq.std(axis=0) + 1e-8
        seq_norm = (seq - mean) / std
        
        x = torch.FloatTensor(seq_norm).unsqueeze(0)  # (1, seq_len, dim)
        
        with torch.no_grad():
            reconstructed, _ = self._model(x)
        
        # MSE
        error = torch.mean((x - reconstructed) ** 2).item()
        return error
    
    def train(self, sequences: np.ndarray, epochs: int = 50,
              lr: float = 1e-3, device: str = "cpu") -> dict:
        """
        Train the autoencoder on historical sequences.
        
        Args:
            sequences: Training data (n_samples, seq_len, feature_dim)
            epochs: Number of training epochs
            lr: Learning rate
            device: Training device
            
        Returns:
            Dict with training history.
        """
        import torch
        import torch.nn as nn
        import torch.optim as optim
        
        self._model = self._build_model().to(device)
        criterion = nn.MSELoss()
        optimizer = optim.Adam(self._model.parameters(), lr=lr)
        
        data = torch.FloatTensor(sequences).to(device)
        n_samples = len(data)
        batch_size = min(32, n_samples)
        
        losses = []
        
        self._model.train()
        for epoch in range(epochs):
            epoch_losses = []
            
            # Shuffle
            indices = torch.randperm(n_samples)
            
            for i in range(0, n_samples, batch_size):
                batch_idx = indices[i:i+batch_size]
                batch = data[batch_idx]
                
                reconstructed, latent = self._model(batch)
                loss = criterion(reconstructed, batch)
                
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                
                epoch_losses.append(loss.item())
            
            avg_loss = np.mean(epoch_losses)
            losses.append(avg_loss)
            
            if (epoch + 1) % 10 == 0:
                logger.info("autoencoder_training",
                            epoch=epoch+1, loss=f"{avg_loss:.6f}")
        
        self._trained = True
        
        # Compute initial error threshold on training data
        self._model.eval()
        with torch.no_grad():
            reconstructed, _ = self._model(data[:500])
            errors = torch.mean((data[:500] - reconstructed) ** 2, dim=(1, 2))
            self._error_threshold = float(np.percentile(errors.numpy(), self.error_threshold_percentile))
        
        logger.info("autoencoder_trained",
                    epochs=epochs, final_loss=f"{losses[-1]:.6f}",
                    error_threshold=f"{self._error_threshold:.6f}")
        
        return {"losses": losses, "error_threshold": self._error_threshold}
    
    def save(self, path: str) -> None:
        """Save model to disk in specified format."""
        import torch
        
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        if self._model is None:
            logger.warning("autoencoder_save_no_model")
            return
        
        if self.model_format == "onnx":
            # Export to ONNX
            dummy_input = torch.randn(1, self.sequence_length, self._feature_dim)
            torch.onnx.export(
                self._model,
                dummy_input,
                str(path.with_suffix('.onnx')),
                input_names=['input'],
                output_names=['reconstructed', 'latent'],
                dynamic_axes={'input': {0: 'batch'}},
                opset_version=14,
            )
            logger.info("autoencoder_saved_onnx", path=str(path.with_suffix('.onnx')))
        else:
            # Save as TorchScript
            scripted = torch.jit.script(self._model)
            torch.jit.save(scripted, str(path.with_suffix('.pt')))
            logger.info("autoencoder_saved_torchscript", path=str(path.with_suffix('.pt')))
    
    def load(self, path: str) -> bool:
        """Load model from disk."""
        import torch
        
        try:
            if self.model_format == "onnx":
                # For inference we'd use onnxruntime, but for training
                # compatibility, load the PyTorch model
                pt_path = str(Path(path).with_suffix('.pt'))
                self._model = torch.jit.load(pt_path)
            else:
                self._model = torch.jit.load(str(Path(path).with_suffix('.pt')))
            
            self._trained = True
            logger.info("autoencoder_loaded", path=path)
            return True
        except FileNotFoundError:
            logger.warning("autoencoder_not_found", path=path)
            return False
        except Exception as e:
            logger.error("autoencoder_load_error", error=str(e))
            return False


# ── Ensemble Anomaly Detector (Multi-Layer Coordinator) ─────────────────────

class AnomalyDetector:
    """
    Multi-layer anomaly detection ensemble.
    
    Coordinates all three detection layers and produces
    a unified anomaly flag with confidence scoring.
    """
    
    def __init__(self, config=None):
        """
        Initialize from config or defaults.
        
        Args:
            config: TradingSystemConfig.anomaly section
        """
        # Default config
        self.layer1 = StatisticalAnomalyDetector()
        self.layer2 = IsolationForestDetector()
        self.layer3 = AutoencoderDetector()
        
        self.cooldown_minutes = 30
        self.max_cooldown_minutes = 45
        
        # Apply config if provided
        if config is not None:
            self._apply_config(config)
        
        # State
        self._anomaly_cooldown_until: Optional[datetime] = None
        self._anomaly_count = 0
        self._last_anomaly: Optional[AnomalyResult] = None
    
    def _apply_config(self, config) -> None:
        """Apply configuration settings."""
        if hasattr(config, 'statistical'):
            s = config.statistical
            self.layer1 = StatisticalAnomalyDetector(
                zscore_window=s.zscore_window,
                zscore_threshold=s.zscore_threshold,
                volatility_spike_mult=s.volatility_spike_mult,
                price_gap_atr_mult=s.price_gap_atr_mult,
                spread_percentile=s.spread_percentile,
            )
        
        if hasattr(config, 'isolation_forest'):
            i = config.isolation_forest
            self.layer2 = IsolationForestDetector(
                n_estimators=i.n_estimators,
                contamination=i.contamination,
                retrain_interval_bars=i.retrain_interval_bars,
            )
        
        if hasattr(config, 'autoencoder'):
            a = config.autoencoder
            self.layer3 = AutoencoderDetector(
                sequence_length=a.sequence_length,
                hidden_dim=a.hidden_dim,
                latent_dim=a.latent_dim,
                error_threshold_percentile=a.error_threshold_percentile,
                model_format=a.model_format,
            )
        
        if hasattr(config, 'cooldown_minutes'):
            self.cooldown_minutes = config.cooldown_minutes
        if hasattr(config, 'max_cooldown_minutes'):
            self.max_cooldown_minutes = config.max_cooldown_minutes
    
    def check(self, price: float, spread: float, atr: float,
              features: dict, timestamp: datetime = None) -> AnomalyResult:
        """
        Run full multi-layer anomaly check.
        
        Args:
            price: Current mid price
            spread: Current spread
            atr: Current ATR
            features: Dict of current features
            timestamp: Current timestamp
            
        Returns:
            Combined AnomalyResult from all layers.
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        # Check if in cooldown
        if self._anomaly_cooldown_until and timestamp < self._anomaly_cooldown_until:
            return AnomalyResult(
                is_anomaly=True,
                trigger_reason="cooldown_active",
                layer_scores={"cooldown": True}
            )
        
        # Layer 1: Statistical
        r1 = self.layer1.update(price, spread, atr, timestamp)
        
        # Layer 2: Isolation Forest
        r2 = self.layer2.update(features)
        
        # Layer 3: Autoencoder (if trained)
        r3 = self.layer3.update(features)
        
        # Ensemble logic: anomaly if ANY layer triggers
        is_anomaly = r1.is_anomaly or r2.is_anomaly or r3.is_anomaly
        
        # Combine scores
        combined_scores = {
            **r1.layer_scores,
            **r2.layer_scores,
            **r3.layer_scores,
        }
        
        # Collect trigger reasons
        reasons = []
        for r, name in [(r1, "statistical"), (r2, "isolation_forest"), (r3, "autoencoder")]:
            if r.is_anomaly and r.trigger_reason:
                reasons.append(f"[{name}] {r.trigger_reason}")
        
        trigger_reason = " | ".join(reasons) if reasons else ""
        
        # Compute combined confidence (max of individual confidences)
        confidence = max(r1.confidence, r2.confidence, r3.confidence)
        
        result = AnomalyResult(
            is_anomaly=is_anomaly,
            timestamp=timestamp,
            layer_scores=combined_scores,
            feature_snapshot=features,
            trigger_reason=trigger_reason,
            confidence=confidence,
        )
        
        if is_anomaly:
            self._anomaly_count += 1
            self._last_anomaly = result
            self._start_cooldown(timestamp)
            logger.warning("anomaly_detected",
                          reason=trigger_reason,
                          confidence=f"{confidence:.2f}",
                          layers=combined_scores)
        
        return result
    
    def _start_cooldown(self, timestamp: datetime) -> None:
        """Start anomaly cooldown period."""
        cooldown = timedelta(minutes=self.cooldown_minutes)
        self._anomaly_cooldown_until = timestamp + cooldown
        logger.info("anomaly_cooldown_started",
                    until=self._anomaly_cooldown_until.isoformat())
    
    def is_cooldown_active(self) -> bool:
        """Check if anomaly cooldown is currently active."""
        if self._anomaly_cooldown_until is None:
            return False
        if datetime.now(timezone.utc) >= self._anomaly_cooldown_until:
            self._anomaly_cooldown_until = None
            return False
        return True
    
    def get_status(self) -> dict:
        """Get anomaly detector status."""
        return {
            'anomaly_count': self._anomaly_count,
            'cooldown_active': self.is_cooldown_active(),
            'cooldown_until': self._anomaly_cooldown_until.isoformat() if self._anomaly_cooldown_until else None,
            'last_anomaly': self._last_anomaly.trigger_reason if self._last_anomaly else None,
        }
    
    def save_models(self, base_path: str = "./models/trained") -> None:
        """Save all trainable models."""
        self.layer2.save(f"{base_path}/isolation_forest.pkl")
        if self.layer3._trained:
            self.layer3.save(f"{base_path}/autoencoder")
    
    def load_models(self, base_path: str = "./models/trained") -> None:
        """Load all trainable models."""
        self.layer2.load(f"{base_path}/isolation_forest.pkl")
        self.layer3.load(f"{base_path}/autoencoder")
