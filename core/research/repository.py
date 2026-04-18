"""
Repository — persiste todos los resultados de investigación.

Storage:
  - SQLite: data/research/experiments.db (consultas rápidas)
  - CSV:    data/research/experiments.csv (análisis en Excel/Pandas)

El feedback loop lee de aquí para saber qué familias funcionan
y priorizar hipótesis similares en el siguiente ciclo.
"""
from __future__ import annotations
import csv
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.research.experiment_runner import ExperimentResult
from core.research.filters import FilterResult

logger = logging.getLogger(__name__)

DB_PATH = Path("data/research/experiments.db")
CSV_PATH = Path("data/research/experiments.csv")


class ResearchRepository:

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def save_experiment(
        self,
        result: ExperimentResult,
        filter_result: FilterResult | None = None,
    ) -> None:
        """Guarda un resultado de experimento con su filtro."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO experiments (
                    hypothesis_id, family, symbol, timeframe, params,
                    sharpe, max_drawdown, profit_factor, win_rate,
                    expectancy, total_trades, total_return_pct,
                    wf_sharpe_mean, wf_sharpe_min, wf_consistency,
                    passed_filters, rejection_reason, ran_at, error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                result.hypothesis_id,
                result.family,
                result.symbol,
                result.timeframe,
                json.dumps(result.params),
                result.sharpe,
                result.max_drawdown,
                result.profit_factor,
                result.win_rate,
                result.expectancy,
                result.total_trades,
                result.total_return_pct,
                result.wf_sharpe_mean,
                result.wf_sharpe_min,
                result.wf_consistency,
                int(filter_result.passed) if filter_result else 0,
                filter_result.rejection_reason if filter_result else "",
                result.ran_at.isoformat(),
                result.error or "",
            ))

    def save_batch(
        self,
        results: list[ExperimentResult],
        filter_results: dict[str, FilterResult] | None = None,
    ) -> None:
        """Guarda un lote de resultados eficientemente."""
        for r in results:
            key = f"{r.hypothesis_id}_{r.symbol}_{r.timeframe}"
            fr = filter_results.get(key) if filter_results else None
            self.save_experiment(r, fr)
        self._export_csv()
        logger.info("Saved %d experiments to repository", len(results))

    def get_approved(self, min_sharpe: float = 0.5) -> list[dict]:
        """Retorna estrategias aprobadas ordenadas por Sharpe WF."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM experiments
                WHERE passed_filters = 1
                  AND wf_sharpe_mean >= ?
                  AND error = ''
                ORDER BY wf_sharpe_mean DESC
            """, (min_sharpe,)).fetchall()
        return [dict(r) for r in rows]

    def get_best_by_family(self) -> dict[str, dict | None]:
        """Retorna la mejor estrategia por familia."""
        families = ["trend", "reversion", "momentum"]
        result = {}
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            for family in families:
                row = conn.execute("""
                    SELECT * FROM experiments
                    WHERE family = ? AND passed_filters = 1
                    ORDER BY wf_sharpe_mean DESC
                    LIMIT 1
                """, (family,)).fetchone()
                result[family] = dict(row) if row else None
        return result

    def feedback_summary(self) -> dict:
        """
        Resumen para el feedback loop:
        - Qué familias funcionan mejor históricamente
        - Qué rangos de parámetros tienen mejor rendimiento
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute("""
                SELECT family,
                       COUNT(*) as total,
                       SUM(passed_filters) as passed,
                       AVG(wf_sharpe_mean) as avg_sharpe,
                       MAX(wf_sharpe_mean) as best_sharpe
                FROM experiments
                WHERE error = ''
                GROUP BY family
                ORDER BY avg_sharpe DESC
            """).fetchall()
        return {
            row[0]: {
                "total_tested": row[1],
                "passed": row[2],
                "pass_rate": round(row[2] / row[1] * 100, 1) if row[1] > 0 else 0,
                "avg_sharpe": round(row[3], 3),
                "best_sharpe": round(row[4], 3),
            }
            for row in rows
        }

    def total_experiments(self) -> int:
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]

    def save_paper_session(
        self,
        hypothesis_id: str,
        symbol: str,
        timeframe: str,
        total_trades: int,
        win_rate_pct: float,
        total_return_pct: float,
        max_drawdown_pct: float,
        profit_factor: float,
        total_pnl: float,
        bars_processed: int,
        mode: str = "paper",
        kill_switch_triggered: bool = False,
        kill_switch_reason: str = "",
    ) -> None:
        """Registra una sesión de paper/live trading para el feedback loop."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT INTO paper_sessions (
                    hypothesis_id, symbol, timeframe, mode,
                    total_trades, win_rate_pct, total_return_pct,
                    max_drawdown_pct, profit_factor, total_pnl,
                    bars_processed, kill_switch_triggered, kill_switch_reason,
                    ran_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                hypothesis_id, symbol, timeframe, mode,
                total_trades, win_rate_pct, total_return_pct,
                max_drawdown_pct, profit_factor, total_pnl,
                bars_processed, int(kill_switch_triggered), kill_switch_reason,
                datetime.now(timezone.utc).isoformat(),
            ))
        logger.info("Paper session saved: %s %s %s | trades=%d | return=%.2f%%",
                    hypothesis_id, symbol, timeframe, total_trades, total_return_pct)

    def get_paper_sessions(
        self,
        hypothesis_id: str | None = None,
        last_n: int = 10,
    ) -> list[dict]:
        """Retorna las últimas N sesiones de paper trading."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            if hypothesis_id:
                rows = conn.execute("""
                    SELECT * FROM paper_sessions
                    WHERE hypothesis_id = ?
                    ORDER BY ran_at DESC LIMIT ?
                """, (hypothesis_id, last_n)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM paper_sessions
                    ORDER BY ran_at DESC LIMIT ?
                """, (last_n,)).fetchall()
        return [dict(r) for r in rows]

    def edge_health(self, hypothesis_id: str, symbol: str) -> dict:
        """
        Compara métricas de backtest vs paper trading para un edge.

        Retorna un dict con:
        - backtest_win_rate, paper_win_rate
        - backtest_profit_factor, paper_profit_factor
        - sessions: número de sesiones paper registradas
        - divergence_flag: True si los resultados divergen significativamente
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            bt = conn.execute("""
                SELECT win_rate, profit_factor, wf_sharpe_mean, total_trades
                FROM experiments
                WHERE hypothesis_id = ? AND symbol = ? AND passed_filters = 1
            """, (hypothesis_id, symbol)).fetchone()

            paper_rows = conn.execute("""
                SELECT win_rate_pct, profit_factor, total_trades, total_return_pct
                FROM paper_sessions
                WHERE hypothesis_id = ? AND symbol = ?
                ORDER BY ran_at DESC LIMIT 5
            """, (hypothesis_id, symbol)).fetchall()

        if not bt or not paper_rows:
            return {"status": "insufficient_data", "sessions": len(paper_rows)}

        paper_wr = sum(r["win_rate_pct"] for r in paper_rows) / len(paper_rows)
        paper_pf = sum(r["profit_factor"] for r in paper_rows if r["profit_factor"] < 999) / max(1, len(paper_rows))
        paper_trades = sum(r["total_trades"] for r in paper_rows)

        # Divergence: paper win rate drops >20 pp vs backtest, or profit factor < 1.0
        wr_divergence = float(bt["win_rate"]) - paper_wr > 20.0
        pf_divergence = paper_trades >= 5 and paper_pf < 1.0

        return {
            "status": "ok" if not (wr_divergence or pf_divergence) else "divergence",
            "backtest_win_rate": round(float(bt["win_rate"]), 1),
            "paper_win_rate": round(paper_wr, 1),
            "backtest_profit_factor": round(float(bt["profit_factor"]), 2),
            "paper_profit_factor": round(paper_pf, 2),
            "backtest_wf_sharpe": round(float(bt["wf_sharpe_mean"]), 3),
            "paper_trades_total": paper_trades,
            "sessions": len(paper_rows),
            "divergence_flag": wr_divergence or pf_divergence,
        }

    def kill_switch_rate(
        self,
        hypothesis_id: str,
        symbol: str,
        last_n: int = 10,
    ) -> float:
        """
        Fracción de las últimas N sesiones en las que se activó el kill switch.

        Returns: 0.0 (nunca) a 1.0 (siempre). Si hay < 3 sesiones devuelve 0.0
        porque el dato no es estadísticamente confiable para tomar decisiones.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute("""
                SELECT kill_switch_triggered
                FROM paper_sessions
                WHERE hypothesis_id = ? AND symbol = ?
                ORDER BY ran_at DESC LIMIT ?
            """, (hypothesis_id, symbol, last_n)).fetchall()

        if len(rows) < 3:
            return 0.0
        return sum(r[0] for r in rows) / len(rows)

    def auto_disable_check(
        self,
        hypothesis_id: str,
        symbol: str,
        kill_switch_threshold: float = 0.8,
        pf_floor: float = 1.0,
        min_sessions: int = 5,
    ) -> tuple[bool, str]:
        """
        Evalúa si una estrategia debe desactivarse automáticamente.

        Criterios de desactivación (cualquiera es suficiente):
        1. Kill switch activado en >= kill_switch_threshold de las últimas 10 sesiones
        2. Profit factor en papel < pf_floor durante las últimas 5 sesiones

        Returns: (debe_desactivar: bool, razón: str)
        """
        kill_rate = self.kill_switch_rate(hypothesis_id, symbol, last_n=10)
        if kill_rate >= kill_switch_threshold:
            return True, f"kill_switch_rate={kill_rate:.0%} >= {kill_switch_threshold:.0%}"

        health = self.edge_health(hypothesis_id, symbol)
        if (
            health.get("sessions", 0) >= min_sessions
            and health.get("status") == "divergence"
            and health.get("paper_profit_factor", 1.0) < pf_floor
        ):
            pf = health["paper_profit_factor"]
            return True, f"edge_divergence: paper_PF={pf:.2f} < {pf_floor}"

        return False, ""

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    hypothesis_id TEXT,
                    family TEXT,
                    symbol TEXT,
                    timeframe TEXT,
                    params TEXT,
                    sharpe REAL,
                    max_drawdown REAL,
                    profit_factor REAL,
                    win_rate REAL,
                    expectancy REAL,
                    total_trades INTEGER,
                    total_return_pct REAL,
                    wf_sharpe_mean REAL,
                    wf_sharpe_min REAL,
                    wf_consistency REAL,
                    passed_filters INTEGER DEFAULT 0,
                    rejection_reason TEXT,
                    ran_at TEXT,
                    error TEXT,
                    PRIMARY KEY (hypothesis_id, symbol, timeframe)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hypothesis_id TEXT,
                    symbol TEXT,
                    timeframe TEXT,
                    mode TEXT DEFAULT 'paper',
                    total_trades INTEGER,
                    win_rate_pct REAL,
                    total_return_pct REAL,
                    max_drawdown_pct REAL,
                    profit_factor REAL,
                    total_pnl REAL,
                    bars_processed INTEGER,
                    kill_switch_triggered INTEGER DEFAULT 0,
                    kill_switch_reason TEXT,
                    ran_at TEXT
                )
            """)

    def _export_csv(self) -> None:
        """Exporta todos los resultados a CSV para análisis externo."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM experiments ORDER BY wf_sharpe_mean DESC").fetchall()

        if not rows:
            return

        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows([dict(r) for r in rows])
