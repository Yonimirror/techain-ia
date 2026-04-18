"""
Tests para el filtrado autónomo de estrategias muertas.

Verifica:
  - kill_switch_rate() calcula correctamente la tasa de kill switches
  - auto_disable_check() identifica estrategias que deben desactivarse
  - Menos de 3 sesiones → no deshabilita (dato insuficiente)
  - Umbral configurable de kill switch rate
  - Umbral de PF < 1.0 con divergencia
"""
from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.research.repository import ResearchRepository


def _make_repo() -> ResearchRepository:
    """Crea un repositorio con DB en memoria temporal."""
    tmp = tempfile.mkdtemp()
    repo = ResearchRepository(db_path=Path(tmp) / "test.db")
    return repo


def _insert_experiment(repo: ResearchRepository, hypothesis_id: str, symbol: str) -> None:
    """Inserta un experimento aprobado en la DB para que edge_health() funcione."""
    with sqlite3.connect(repo._db_path) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO experiments (
                hypothesis_id, family, symbol, timeframe, params,
                sharpe, max_drawdown, profit_factor, win_rate,
                expectancy, total_trades, total_return_pct,
                wf_sharpe_mean, wf_sharpe_min, wf_consistency,
                passed_filters, ran_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            hypothesis_id, "reversion", symbol, "1d", "{}",
            1.5, 5.0, 2.5, 70.0,
            50.0, 20, 15.0,
            0.8, 0.3, 0.8,
            1, datetime.now(timezone.utc).isoformat(),
        ))


def _add_sessions(
    repo: ResearchRepository,
    hypothesis_id: str,
    symbol: str,
    n: int,
    kill_switch: bool,
    profit_factor: float = 2.0,
    win_rate: float = 70.0,
) -> None:
    """Añade N sesiones de paper trading con parámetros fijos."""
    for i in range(n):
        repo.save_paper_session(
            hypothesis_id=hypothesis_id,
            symbol=symbol,
            timeframe="1d",
            total_trades=10,
            win_rate_pct=win_rate,
            total_return_pct=5.0,
            max_drawdown_pct=2.0,
            profit_factor=profit_factor,
            total_pnl=500.0,
            bars_processed=500,
            kill_switch_triggered=kill_switch,
            kill_switch_reason="Daily loss limit" if kill_switch else "",
        )


# ── kill_switch_rate() ────────────────────────────────────────────────────────

class TestKillSwitchRate:
    def test_zero_sessions_returns_zero(self):
        repo = _make_repo()
        rate = repo.kill_switch_rate("nonexistent", "BTC")
        assert rate == 0.0

    def test_less_than_3_sessions_returns_zero(self):
        """Con < 3 sesiones no hay suficiente dato estadístico."""
        repo = _make_repo()
        _add_sessions(repo, "rsi_btc", "BTC", n=2, kill_switch=True)
        rate = repo.kill_switch_rate("rsi_btc", "BTC")
        assert rate == 0.0

    def test_all_kills_returns_one(self):
        repo = _make_repo()
        _add_sessions(repo, "rsi_btc", "BTC", n=10, kill_switch=True)
        rate = repo.kill_switch_rate("rsi_btc", "BTC")
        assert rate == 1.0

    def test_no_kills_returns_zero(self):
        repo = _make_repo()
        _add_sessions(repo, "rsi_btc", "BTC", n=10, kill_switch=False)
        rate = repo.kill_switch_rate("rsi_btc", "BTC")
        assert rate == 0.0

    def test_half_kills_returns_half(self):
        repo = _make_repo()
        _add_sessions(repo, "rsi_btc", "BTC", n=5, kill_switch=True)
        _add_sessions(repo, "rsi_btc", "BTC", n=5, kill_switch=False)
        rate = repo.kill_switch_rate("rsi_btc", "BTC")
        assert abs(rate - 0.5) < 0.01

    def test_last_n_only_considers_recent(self):
        """Solo mira las últimas N sesiones, no el histórico completo."""
        repo = _make_repo()
        # 10 sesiones antiguas sin kill switch
        _add_sessions(repo, "rsi_btc", "BTC", n=10, kill_switch=False)
        # 5 sesiones recientes todas con kill switch
        _add_sessions(repo, "rsi_btc", "BTC", n=5, kill_switch=True)
        # last_n=5 → mira solo las 5 más recientes → 100% kills
        rate = repo.kill_switch_rate("rsi_btc", "BTC", last_n=5)
        assert rate == 1.0

    def test_eth_independent_from_btc(self):
        """Kill switch rate de BTC no contamina ETH."""
        repo = _make_repo()
        _add_sessions(repo, "rsi_eth", "ETH", n=10, kill_switch=True)
        _add_sessions(repo, "rsi_btc", "BTC", n=10, kill_switch=False)
        assert repo.kill_switch_rate("rsi_eth", "ETH") == 1.0
        assert repo.kill_switch_rate("rsi_btc", "BTC") == 0.0


# ── auto_disable_check() ──────────────────────────────────────────────────────

class TestAutoDisableCheck:
    def test_no_sessions_no_disable(self):
        """Sin historial de paper trading, no deshabilita."""
        repo = _make_repo()
        _insert_experiment(repo, "rsi_btc", "BTC")
        should, reason = repo.auto_disable_check("rsi_btc", "BTC")
        assert should is False
        assert reason == ""

    def test_high_kill_switch_rate_disables(self):
        """Kill switch en >= 80% de sesiones → deshabilitar."""
        repo = _make_repo()
        _insert_experiment(repo, "rsi_btc", "BTC")
        _add_sessions(repo, "rsi_btc", "BTC", n=10, kill_switch=True)
        should, reason = repo.auto_disable_check("rsi_btc", "BTC", kill_switch_threshold=0.8)
        assert should is True
        assert "kill_switch_rate" in reason

    def test_low_kill_switch_rate_no_disable(self):
        """Kill switch en 30% no supera el umbral del 80%."""
        repo = _make_repo()
        _insert_experiment(repo, "rsi_btc", "BTC")
        _add_sessions(repo, "rsi_btc", "BTC", n=3, kill_switch=True)
        _add_sessions(repo, "rsi_btc", "BTC", n=7, kill_switch=False)
        should, reason = repo.auto_disable_check("rsi_btc", "BTC", kill_switch_threshold=0.8)
        assert should is False

    def test_pf_below_floor_with_divergence_disables(self):
        """PF en papel < 1.0 con suficientes sesiones → deshabilitar."""
        repo = _make_repo()
        _insert_experiment(repo, "rsi_eth_bad", "ETH")
        # Insertar sesiones con WR muy baja para generar divergencia (BT=70%, papel=20%)
        _add_sessions(
            repo, "rsi_eth_bad", "ETH",
            n=10, kill_switch=False,
            profit_factor=0.6,  # PF < 1.0
            win_rate=20.0,      # divergencia vs BT 70%
        )
        should, reason = repo.auto_disable_check(
            "rsi_eth_bad", "ETH",
            kill_switch_threshold=0.8,
            pf_floor=1.0,
            min_sessions=5,
        )
        assert should is True
        assert "edge_divergence" in reason

    def test_pf_above_floor_no_disable(self):
        """PF en papel >= 1.0 no deshabilita aunque haya divergencia WR."""
        repo = _make_repo()
        _insert_experiment(repo, "rsi_btc_ok", "BTC")
        _add_sessions(
            repo, "rsi_btc_ok", "BTC",
            n=10, kill_switch=False,
            profit_factor=1.5,  # PF >= 1.0
            win_rate=50.0,
        )
        should, reason = repo.auto_disable_check(
            "rsi_btc_ok", "BTC",
            kill_switch_threshold=0.8,
            pf_floor=1.0,
        )
        assert should is False

    def test_few_sessions_no_disable_even_with_bad_pf(self):
        """Con < min_sessions, no deshabilita aunque el PF sea malo."""
        repo = _make_repo()
        _insert_experiment(repo, "rsi_new", "BTC")
        _add_sessions(
            repo, "rsi_new", "BTC",
            n=3, kill_switch=False,
            profit_factor=0.5, win_rate=10.0,
        )
        should, _ = repo.auto_disable_check(
            "rsi_new", "BTC",
            min_sessions=5,  # requiere 5 sesiones mínimo
        )
        assert should is False

    def test_custom_threshold(self):
        """Umbral configurable: 50% kill rate con threshold=0.5 → deshabilitar."""
        repo = _make_repo()
        _insert_experiment(repo, "rsi_btc", "BTC")
        _add_sessions(repo, "rsi_btc", "BTC", n=5, kill_switch=True)
        _add_sessions(repo, "rsi_btc", "BTC", n=5, kill_switch=False)
        # Con threshold=0.8 → no deshabilita (50% < 80%)
        should_08, _ = repo.auto_disable_check("rsi_btc", "BTC", kill_switch_threshold=0.8)
        # Con threshold=0.5 → sí deshabilita (50% >= 50%)
        should_05, _ = repo.auto_disable_check("rsi_btc", "BTC", kill_switch_threshold=0.5)
        assert should_08 is False
        assert should_05 is True

    def test_eth_1d_case_replicates_real_scenario(self):
        """
        Replica el caso real: ETH 1d con 51/51 kill switches.
        Debe desactivarse automáticamente.
        """
        repo = _make_repo()
        _insert_experiment(repo, "reversion_rsi7_os35_ob60_sl3_ema200", "ETH")
        _add_sessions(
            repo, "reversion_rsi7_os35_ob60_sl3_ema200", "ETH",
            n=10, kill_switch=True,
            profit_factor=0.45, win_rate=20.0,
        )
        should, reason = repo.auto_disable_check(
            "reversion_rsi7_os35_ob60_sl3_ema200", "ETH"
        )
        assert should is True
        assert "kill_switch_rate=100%" in reason
