from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RiskConfig:
    # Capital limits
    max_position_size_pct: float = 5.0      # max % of equity per position
    max_total_exposure_pct: float = 80.0    # max % of equity deployed

    # Drawdown protection
    max_drawdown_pct: float = 15.0          # kill switch triggers at this drawdown %
    soft_drawdown_pct: float = 10.0         # reduce sizes at this drawdown %

    # Kelly criterion
    kelly_fraction: float = 0.25            # fractional Kelly (25% full Kelly)
    kelly_max_pct: float = 10.0             # hard cap regardless of Kelly

    # Signal filters
    min_signal_strength: float = 0.3        # ignore weak signals
    max_correlated_positions: int = 3       # max simultaneous same-direction positions

    # Per-symbol limits
    max_position_per_symbol_pct: float = 5.0

    # Session limits
    max_trades_per_day: int = 20
    max_daily_loss_pct: float = 5.0        # kill switch if daily loss >= X% of equity
    max_consecutive_losses: int = 100       # increased from 5 to allow backtesting to complete

    # Edge monitor (EWMA degradation detection)
    edge_monitor_window: int = 20          # EWMA span — α = 2 / (window + 1)
    edge_min_win_rate: float = 0.40        # reduce sizing if EWMA WR drops below this
    edge_min_profit_factor: float = 1.10   # reduce sizing if EWMA PF drops below this
    edge_min_trades_to_act: int = 15       # minimum trades before monitor influences sizing

    @classmethod
    def from_dict(cls, d: dict) -> "RiskConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
