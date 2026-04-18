"""
Reporter — genera el informe semanal legible.

Formato:
  - Consola (rich): tabla coloreada con top estrategias
  - Archivo: data/research/report_YYYY-MM-DD.md

El informe responde: "¿qué estrategia debería activar esta semana?"
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from core.research.repository import ResearchRepository


REPORT_DIR = Path("data/research/reports")


def generate_report(repo: ResearchRepository, top_n: int = 5) -> str:
    """
    Genera el informe y lo guarda en disco.
    Returns el informe como string.
    """
    approved = repo.get_approved(min_sharpe=0.5)
    feedback = repo.feedback_summary()
    total = repo.total_experiments()
    best_by_family = repo.get_best_by_family()

    report = _build_report(approved[:top_n], feedback, total, best_by_family)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = REPORT_DIR / f"report_{date_str}.md"
    report_path.write_text(report, encoding="utf-8")

    return report


def _build_report(
    top_strategies: list[dict],
    feedback: dict,
    total_experiments: int,
    best_by_family: dict,
) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# INFORME DE INVESTIGACIÓN — {date_str}",
        "",
        f"**Experimentos totales ejecutados:** {total_experiments}",
        f"**Estrategias aprobadas (3 filtros):** {len(top_strategies)}",
        "",
        "---",
        "",
        "## TOP ESTRATEGIAS",
        "",
    ]

    if not top_strategies:
        lines += [
            "> No hay estrategias aprobadas aún.",
            "> Ejecuta más experimentos o revisa los umbrales de filtros.",
            "",
        ]
    else:
        for i, s in enumerate(top_strategies, 1):
            params = json.loads(s["params"]) if isinstance(s["params"], str) else s["params"]
            lines += [
                f"### #{i} — {s['hypothesis_id']}",
                f"**Activo:** {s['symbol']} | **Timeframe:** {s['timeframe']} | **Familia:** {s['family']}",
                "",
                f"| Métrica | Valor |",
                f"|---------|-------|",
                f"| Sharpe (WF) | {s['wf_sharpe_mean']:.3f} |",
                f"| Consistencia | {s['wf_consistency']:.0%} |",
                f"| Max Drawdown | {s['max_drawdown']:.1f}% |",
                f"| Profit Factor | {s['profit_factor']:.2f} |",
                f"| Win Rate | {s['win_rate']:.1f}% |",
                f"| Total Trades | {s['total_trades']} |",
                f"| Return Total | {s['total_return_pct']:+.1f}% |",
                "",
                f"**Parámetros:** `{params}`",
                "",
            ]

    lines += [
        "---",
        "",
        "## RENDIMIENTO POR FAMILIA",
        "",
        "| Familia | Testeadas | Aprobadas | Tasa aprobación | Sharpe promedio | Mejor Sharpe |",
        "|---------|-----------|-----------|-----------------|-----------------|--------------|",
    ]

    for family, stats in feedback.items():
        lines.append(
            f"| {family} | {stats['total_tested']} | {stats['passed']} | "
            f"{stats['pass_rate']}% | {stats['avg_sharpe']:.3f} | {stats['best_sharpe']:.3f} |"
        )

    lines += [
        "",
        "---",
        "",
        "## MEJOR POR FAMILIA",
        "",
    ]

    for family, best in best_by_family.items():
        if best:
            lines.append(
                f"- **{family.upper()}**: `{best['hypothesis_id']}` "
                f"en {best['symbol']} {best['timeframe']} "
                f"(Sharpe WF: {best['wf_sharpe_mean']:.3f})"
            )
        else:
            lines.append(f"- **{family.upper()}**: sin estrategia aprobada aún")

    lines += [
        "",
        "---",
        "",
        "## DECISIÓN RECOMENDADA",
        "",
    ]

    if top_strategies:
        best = top_strategies[0]
        lines += [
            f"> Activar **{best['hypothesis_id']}** en {best['symbol']} {best['timeframe']}",
            f"> Sharpe walk-forward: {best['wf_sharpe_mean']:.3f} | "
            f"Consistencia: {best['wf_consistency']:.0%} | "
            f"Max DD: {best['max_drawdown']:.1f}%",
            "",
            "> **La decisión final es tuya. El sistema recomienda, el humano decide.**",
        ]
    else:
        lines += [
            "> No hay suficientes datos para hacer una recomendación.",
            "> Ejecuta el Research Engine con más activos o parámetros.",
        ]

    return "\n".join(lines)


def print_report_console(report: str) -> None:
    """Imprime el informe en consola con formato básico."""
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        console = Console(force_terminal=False, no_color=True)
        console.print(Markdown(report))
    except Exception:
        print(report)
