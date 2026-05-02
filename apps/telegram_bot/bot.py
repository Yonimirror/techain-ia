"""
Telegram Bot — on-demand backtesting and system monitoring.

Commands:
    /test RSI 14 oversold 30 ETH 4h 6m  → run research hypothesis
    /status                              → portfolio summary
    /health                              → market health check
    /strategies                          → list active strategies
    /kill                                → activate kill switch
    /unkill                              → deactivate kill switch

Usage:
    python -m apps.telegram_bot.bot

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from core.domain.value_objects import Symbol, Timeframe
from core.research import (
    ExperimentRunner, generate_hypotheses,
    ResearchRepository, load_multiple,
)

logger = logging.getLogger(__name__)


def _load_env() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


class TelegramNotifier:
    """
    Sends messages to a Telegram chat. Standalone — no bot polling required.
    Use this from the trader service to push notifications.
    """

    def __init__(self, token: str = "", chat_id: str = "") -> None:
        self._token = token or BOT_TOKEN
        self._chat_id = chat_id or CHAT_ID

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    async def send(self, message: str) -> bool:
        if not self.enabled:
            logger.debug("Telegram not configured — skipping notification")
            return False
        try:
            import httpx
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json={
                    "chat_id": self._chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                })
                if resp.status_code == 200:
                    return True
                logger.warning("Telegram send failed: %s", resp.text)
                return False
        except Exception as exc:
            logger.warning("Telegram send error: %s", exc)
            return False

    async def send_trade_alert(self, action: str, symbol: str, side: str, qty: str, price: str, pnl: str = "") -> bool:
        pnl_line = f"\n💰 PnL: {pnl}" if pnl else ""
        msg = (
            f"🔔 *{action}*\n"
            f"Symbol: `{symbol}`\n"
            f"Side: {side} | Qty: {qty}\n"
            f"Price: {price}{pnl_line}"
        )
        return await self.send(msg)

    async def send_kill_switch_alert(self, reason: str) -> bool:
        return await self.send(f"🚨 *KILL SWITCH ACTIVATED*\n{reason}")

    async def send_positions_summary(
        self,
        binance_positions: list[dict],
        ibkr_positions: list[dict],
        rsi_status: dict,
    ) -> bool:
        from datetime import date
        lines = [f"📊 *Informe diario — {date.today().strftime('%d %b %Y')}*\n"]

        # ── BINANCE LIVE ────────────────────────────────────────────────
        lines.append("🟡 *BINANCE — LIVE*")
        if binance_positions:
            for p in binance_positions:
                entry = p["entry_price"]
                current = p["current_price"]
                qty = p["quantity"]
                pnl_eur = (current - entry) * qty
                pnl_pct = (current - entry) / entry * 100
                sign = "+" if pnl_eur >= 0 else ""
                arrow = "📈" if pnl_eur >= 0 else "📉"
                oversold = p.get("oversold_threshold")
                stop = p.get("stop_loss_price")
                reason = f"RSI cayó por debajo de {oversold} (zona de pánico)" if oversold else "señal de entrada activada"
                stop_str = f" | Stop: {stop:,.0f} EUR" if stop else ""
                lines.append(
                    f"{arrow} *{p['symbol']}* — posición abierta\n"
                    f"  Entrada: {entry:,.2f} EUR | Actual: {current:,.2f} EUR\n"
                    f"  P&L: `{sign}{pnl_eur:.2f} EUR ({sign}{pnl_pct:.2f}%)`{stop_str}\n"
                    f"  _Motivo: {reason}_"
                )
        else:
            # Show why no position for crypto assets
            crypto = {s: v for s, v in rsi_status.items() if s in {"BTC", "ETH", "SOL", "BNB"}}
            if crypto:
                lines.append("Sin posiciones abiertas")
                for sym, rsi in sorted(crypto.items()):
                    rsi_str = f"RSI {rsi:.0f}" if rsi else "RSI desconocido"
                    lines.append(f"  • {sym}: {rsi_str} — esperando caída a zona <35")
            else:
                lines.append("Sin posiciones abiertas")

        lines.append("")

        # ── IBKR PAPER ──────────────────────────────────────────────────
        lines.append("🔵 *IBKR — PAPER*")
        if ibkr_positions:
            for p in ibkr_positions:
                entry = p["entry_price"]
                current = p["current_price"]
                qty = p["quantity"]
                pnl = (current - entry) * qty
                pnl_pct = (current - entry) / entry * 100
                sign = "+" if pnl >= 0 else ""
                arrow = "📈" if pnl >= 0 else "📉"
                oversold = p.get("oversold_threshold")
                stop = p.get("stop_loss_price")
                reason = f"RSI cayó por debajo de {oversold}" if oversold else "señal de entrada activada"
                stop_str = f" | Stop: {stop:,.2f} USD" if stop else ""
                lines.append(
                    f"{arrow} *{p['symbol']}* ({p['timeframe']}) — posición abierta\n"
                    f"  Entrada: {entry:,.2f} | Actual: {current:,.2f}\n"
                    f"  P&L: `{sign}{pnl:.2f} ({sign}{pnl_pct:.2f}%)`{stop_str}\n"
                    f"  _Motivo: {reason}_"
                )
        else:
            equities = {s: v for s, v in rsi_status.items() if s not in {"BTC", "ETH", "SOL", "BNB"}}
            if equities:
                lines.append("Sin posiciones abiertas (modo paper)")
                for sym, rsi in sorted(equities.items()):
                    rsi_str = f"RSI {rsi:.0f}" if rsi else "RSI —"
                    lines.append(f"  • {sym}: {rsi_str}")
            else:
                lines.append("Sin posiciones abiertas (modo paper)")

        lines.append("")
        lines.append("_/estrategias para ver todas las estrategias activas_")
        return await self.send("\n".join(lines))

    async def send_daily_summary(self, results: list[dict]) -> bool:
        """Legacy — kept for compatibility. Use send_positions_summary() instead."""
        if not results:
            return await self.send("📊 *Daily Summary*\nNo strategies ran today.")
        lines = ["📊 *Daily Summary*\n"]
        for r in results:
            emoji = "✅" if r.get("total_return_pct", 0) >= 0 else "❌"
            lines.append(
                f"{emoji} `{r['hypothesis_id'][:30]}` | {r['symbol']} {r['timeframe']}\n"
                f"   Return: {r.get('total_return_pct', 0):.2f}% | Trades: {r.get('total_trades', 0)} | "
                f"WR: {r.get('win_rate_pct', 0):.1f}%"
            )
        return await self.send("\n".join(lines))


def _parse_test_command(text: str) -> dict | None:
    """
    Parse /test command into research parameters.

    Examples:
        /test RSI 14 oversold 30 ETH 4h 6m
        /test bollinger bb20 std2 BTC 1d 1y
        /test EMA 9 21 BTC 4h 6m
    """
    text = text.strip()
    if not text.startswith("/test"):
        return None

    parts = text.split()
    if len(parts) < 4:
        return None

    result = {
        "strategy": None,
        "params": {},
        "symbol": "BTC",
        "timeframe": "4h",
        "period_months": 6,
    }

    i = 1  # skip /test
    while i < len(parts):
        p = parts[i].upper()

        # Strategy type
        if p in ("RSI", "REVERSION"):
            result["strategy"] = "reversion"
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                result["params"]["rsi_period"] = int(parts[i + 1])
                i += 1
        elif p in ("EMA", "TREND", "MOMENTUM"):
            result["strategy"] = "trend"
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                result["params"]["ema_fast"] = int(parts[i + 1])
                i += 1
                if i + 1 < len(parts) and parts[i + 1].isdigit():
                    result["params"]["ema_slow"] = int(parts[i + 1])
                    i += 1
        elif p in ("BOLLINGER", "BB"):
            result["strategy"] = "bollinger"

        # Named params
        elif p == "OVERSOLD" and i + 1 < len(parts):
            result["params"]["oversold"] = int(parts[i + 1])
            i += 1
        elif p == "OVERBOUGHT" and i + 1 < len(parts):
            result["params"]["overbought"] = int(parts[i + 1])
            i += 1

        # Bollinger params
        elif p.startswith("BB") and p[2:].isdigit():
            result["params"]["bb_period"] = int(p[2:])
        elif p.startswith("STD") and re.match(r"[\d.]+", p[3:]):
            result["params"]["bb_std"] = float(p[3:])

        # Symbol
        elif p in ("BTC", "ETH", "SOL", "SPY", "AAPL", "NVDA"):
            result["symbol"] = p

        # Timeframe
        elif p in ("1D", "4H", "1H"):
            result["timeframe"] = p.lower()

        # Period
        elif re.match(r"^\d+[MY]$", p):
            num = int(p[:-1])
            if p.endswith("Y"):
                result["period_months"] = num * 12
            else:
                result["period_months"] = num

        i += 1

    if not result["strategy"]:
        return None

    return result


async def _run_ondemand_test(params: dict) -> str:
    """Run a quick backtest from Telegram command and return formatted result."""
    from core.research.hypothesis import Hypothesis
    import yaml

    config_path = Path("config/research.yaml")
    config = {}
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

    symbol = params["symbol"]
    timeframe = params["timeframe"]
    months = params["period_months"]

    # Load data
    exchange = "CRYPTO" if symbol in ("BTC", "ETH", "SOL") else "NYSE"
    start = datetime.now(timezone.utc) - timedelta(days=30 * months)

    try:
        data_list = load_multiple(
            [(symbol, exchange)],
            [Timeframe(timeframe)],
            start=start,
        )
        if not data_list:
            return f"❌ No data for {symbol} {timeframe}"
    except Exception as e:
        return f"❌ Data load failed: {e}"

    # Generate hypotheses filtered by strategy type
    all_hypotheses = generate_hypotheses(config.get("hypotheses"))
    family = params["strategy"]
    filtered = [h for h in all_hypotheses if h.family == family]

    # Further filter by user params if provided
    user_params = params.get("params", {})
    if user_params:
        def matches(h: Hypothesis) -> bool:
            for k, v in user_params.items():
                if k in h.params and h.params[k] != v:
                    return False
            return True
        filtered = [h for h in filtered if matches(h)]

    if not filtered:
        return f"❌ No hypotheses match: family={family}, params={user_params}"

    # Run experiments
    runner = ExperimentRunner(config.get("backtest", {}))
    results = runner.run_all(filtered, data_list)

    if not results:
        return "❌ No results"

    # Sort by Sharpe
    results.sort(key=lambda r: r.sharpe, reverse=True)
    top = results[:5]

    lines = [
        f"📈 *Research: {family.upper()} on {symbol} {timeframe} ({months}m)*\n",
        f"Tested: {len(results)} hypotheses\n",
    ]
    for i, r in enumerate(top, 1):
        emoji = "🟢" if r.sharpe > 0.5 else ("🟡" if r.sharpe > 0 else "🔴")
        lines.append(
            f"{emoji} #{i} `{r.hypothesis_id[:35]}`\n"
            f"   Sharpe: {r.sharpe:.2f} | Trades: {r.total_trades} | "
            f"WR: {r.win_rate:.1f}% | DD: {r.max_drawdown:.1f}%"
        )

    return "\n".join(lines)


async def run_bot() -> None:
    """
    Main bot loop using long polling.
    Requires python-telegram-bot or manual polling.
    We use raw httpx to avoid extra dependencies.
    """
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in .env — bot cannot start")
        return

    import httpx

    base_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    offset = 0
    notifier = TelegramNotifier()

    logger.info("Telegram bot starting... Listening for commands.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                resp = await client.get(
                    f"{base_url}/getUpdates",
                    params={"offset": offset, "timeout": 20},
                )
                if resp.status_code != 200:
                    await asyncio.sleep(5)
                    continue

                data = resp.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    # Security: only respond to authorized chat
                    if CHAT_ID and chat_id != CHAT_ID:
                        continue

                    if text.startswith("/test"):
                        await notifier.send("⏳ Running research... this may take a few minutes.")
                        params = _parse_test_command(text)
                        if params:
                            result = await _run_ondemand_test(params)
                            await notifier.send(result)
                        else:
                            await notifier.send(
                                "❓ Usage: `/test RSI 14 oversold 30 ETH 4h 6m`\n"
                                "Strategies: RSI, EMA, BOLLINGER\n"
                                "Symbols: BTC, ETH, SOL\n"
                                "Timeframes: 1d, 4h, 1h"
                            )

                    elif text.startswith("/status"):
                        repo = ResearchRepository()
                        approved = repo.get_approved()
                        if not approved:
                            await notifier.send("No hay estrategias aprobadas.")
                        else:
                            from core.portfolio_engine.persistence import load_state, rebuild_portfolio
                            from datetime import datetime, timezone

                            now_utc = datetime.now(timezone.utc)
                            hora_utc = now_utc.strftime("%H:%M UTC")

                            CRYPTO = {'BTC', 'ETH', 'SOL', 'BNB'}
                            IBKR_SYMS = {'SPY', 'QQQ', 'GLD', 'TLT', 'XLF', 'XLE', 'SMH', 'XLI',
                                         'NVDA', 'AVGO', 'MSFT', 'FCX', 'TSM', 'AAPL'}

                            # ── Saldos reales ──────────────────────────────────
                            binance_balance = None
                            ibkr_balance = None
                            try:
                                import os
                                from binance.client import Client as BClient
                                bc = BClient(os.environ.get("BINANCE_API_KEY", ""),
                                             os.environ.get("BINANCE_SECRET_KEY", ""))
                                acc = bc.get_account()
                                bals = {b["asset"]: float(b["free"]) for b in acc["balances"]}
                                binance_balance = bals.get("USDT", 0) or bals.get("EUR", 0)
                            except Exception:
                                pass

                            try:
                                from ib_insync import IB, util
                                util.patchAsyncio()
                                ib = IB()
                                ib.connect("127.0.0.1", int(os.environ.get("IBKR_PORT", "7498")),
                                           clientId=10, timeout=10)
                                for a in ib.accountSummary():
                                    if a.tag == "NetLiquidation" and a.currency in ("USD", "EUR"):
                                        ibkr_balance = float(a.value)
                                ib.disconnect()
                            except Exception:
                                pass

                            # ── Cabecera ───────────────────────────────────────
                            lines = [f"📊 *Estado del sistema* — {hora_utc}\n"]

                            # Saldos reales
                            if binance_balance is not None:
                                lines.append(f"🟡 *Binance LIVE* — {binance_balance:.2f} EUR/USDT")
                            if ibkr_balance is not None:
                                lines.append(f"🔵 *IBKR paper* — {ibkr_balance:,.0f} USD")
                            lines.append("─────────────────────────")

                            # ── Estrategias por broker ─────────────────────────
                            binance_lines = []
                            ibkr_lines = []
                            total_trades = 0

                            for row in approved[:30]:
                                h_id = row["hypothesis_id"]
                                sym  = row["symbol"]
                                tf   = row["timeframe"]
                                key  = f"{h_id}_{sym}_{tf}"

                                saved = load_state(key)
                                if not saved:
                                    continue
                                port    = rebuild_portfolio(saved)
                                s       = port.summary()
                                trades  = s.get("trades", 0)
                                if trades == 0:
                                    continue
                                wr      = s.get("win_rate", 0.0)
                                pf      = s.get("profit_factor", 0.0)
                                cap     = float(saved.initial_capital)
                                equity  = float(saved.cash)
                                ret_pct = (equity - cap) / cap * 100
                                ks      = saved.risk_state.get("kill_switch_active", False) if saved.risk_state else False
                                total_trades += trades

                                ks_icon  = "🔴" if ks else "🟢"
                                wr_icon  = "✅" if wr >= 60 else ("⚠️" if wr >= 45 else "❌")
                                ret_icon = "📈" if ret_pct >= 0 else "📉"

                                entry = (
                                    f"{ks_icon} *{sym}* `{h_id[:28]}` {tf}\n"
                                    f"   {wr_icon} WR: {wr:.0f}% | {ret_icon} {ret_pct:+.1f}% | "
                                    f"Trades: {trades} | PF: {pf:.2f}"
                                )

                                if sym in CRYPTO:
                                    binance_lines.append(entry)
                                else:
                                    ibkr_lines.append(entry)

                            if binance_lines:
                                lines.append("\n🟡 *BINANCE — LIVE*")
                                lines.extend(binance_lines)

                            if ibkr_lines:
                                lines.append("\n🔵 *IBKR — PAPER*")
                                lines.extend(ibkr_lines)

                            lines.append(
                                f"\n─────────────────────────\n"
                                f"📦 Trades totales: *{total_trades}*"
                            )

                            msg = "\n".join(lines)
                            if len(msg) > 4000:
                                mid = len(lines) // 2
                                await notifier.send("\n".join(lines[:mid]))
                                await notifier.send("\n".join(lines[mid:]))
                            else:
                                await notifier.send(msg)

                    elif text.startswith("/health"):
                        from core.market_health import MarketHealthDetector
                        detector = MarketHealthDetector()
                        level_emoji = {"HEALTHY": "🟢", "DEGRADED": "🟡", "CRITICAL": "🔴"}
                        all_healthy = True
                        lines = ["🏥 *Salud del mercado*\n"]

                        for sym in ("BTC", "ETH", "SOL"):
                            status = await detector.check(sym)
                            lvl = status.level.value
                            if lvl != "HEALTHY":
                                all_healthy = False
                            icon = level_emoji.get(lvl, "⚪")

                            # Spread interpretation
                            if status.spread_bps < 1:
                                spread_txt = f"{status.spread_bps:.1f} bps — liquidez excelente"
                            elif status.spread_bps < 5:
                                spread_txt = f"{status.spread_bps:.1f} bps — liquidez aceptable"
                            else:
                                spread_txt = f"{status.spread_bps:.1f} bps — coste alto, cuidado"

                            # Volume interpretation
                            vol = status.volume_24h_usd
                            if vol > 1_000_000_000:
                                vol_txt = f"${vol/1e9:.1f}B — mercado profundo"
                            elif vol > 200_000_000:
                                vol_txt = f"${vol/1e6:.0f}M — volumen sólido"
                            else:
                                vol_txt = f"${vol/1e6:.0f}M — volumen bajo, riesgo slippage"

                            # Funding interpretation for mean reversion
                            funding_txt = ""
                            if status.funding_rate is not None:
                                fr = status.funding_rate
                                if fr < -0.0005:
                                    funding_txt = f"Funding: {fr:.4f} — shorts pagando, favorece rebote"
                                elif fr > 0.0005:
                                    funding_txt = f"Funding: {fr:.4f} — longs pagando, riesgo squeeze"
                                else:
                                    funding_txt = f"Funding: {fr:.4f} — mercado neutral"

                            lines.append(
                                f"{icon} *{sym}* ({lvl})\n"
                                f"   Spread: {spread_txt}\n"
                                f"   Vol: {vol_txt}\n"
                                f"   {funding_txt}"
                            )
                            if status.issues:
                                lines.append(f"   ⚠️ {', '.join(status.issues)}")

                        # Global verdict
                        if all_healthy:
                            lines.append("\n✅ *Condiciones optimas para operar*")
                        else:
                            lines.append("\n⚠️ *Algún mercado degradado — el sistema reduce exposición automáticamente*")

                        await notifier.send("\n".join(lines))

                    elif text.startswith("/estrategias"):
                        repo = ResearchRepository()
                        approved = repo.get_approved()
                        if not approved:
                            await notifier.send("No hay estrategias aprobadas.")
                        else:
                            from core.portfolio_engine.persistence import load_state
                            lines = [f"📋 *Estrategias activas ({len(approved)})*\n"]
                            CRYPTO = {"BTC", "ETH", "SOL", "BNB"}
                            crypto_strats = [(s, sym, tf) for s, sym, tf in approved if sym in CRYPTO]
                            equity_strats = [(s, sym, tf) for s, sym, tf in approved if sym not in CRYPTO]
                            if crypto_strats:
                                lines.append("🟡 *BINANCE — Crypto*")
                                for strat, sym, tf in crypto_strats:
                                    meta = strat.metadata if hasattr(strat, "metadata") else {}
                                    hid = meta.get("hypothesis_id", strat.strategy_id)
                                    session_id = f"{hid}_{sym}_{tf}"
                                    state = load_state(session_id)
                                    has_pos = "🔵" if (state and state.positions) else "⚪"
                                    lines.append(f"  {has_pos} `{hid}` | {sym} {tf}")
                            if equity_strats:
                                lines.append("\n🔵 *IBKR — Equities/ETFs*")
                                for strat, sym, tf in equity_strats:
                                    meta = strat.metadata if hasattr(strat, "metadata") else {}
                                    hid = meta.get("hypothesis_id", strat.strategy_id)
                                    session_id = f"{hid}_{sym}_{tf}"
                                    state = load_state(session_id)
                                    has_pos = "🔵" if (state and state.positions) else "⚪"
                                    lines.append(f"  {has_pos} `{hid[:35]}` | {sym} {tf}")
                            lines.append("\n🔵 = posición abierta | ⚪ = en espera")
                            msg = "\n".join(lines)
                            if len(msg) > 4000:
                                mid = len(lines) // 2
                                await notifier.send("\n".join(lines[:mid]))
                                await notifier.send("\n".join(lines[mid:]))
                            else:
                                await notifier.send(msg)

                    elif text.startswith("/help"):
                        await notifier.send(
                            "🤖 *Techain-IA Bot*\n\n"
                            "/status — Posiciones abiertas + RSI actual\n"
                            "/estrategias — Lista completa de estrategias activas\n"
                            "/health — Salud del mercado (spread, volumen, funding)\n"
                            "/test — Lanzar research on-demand\n"
                            "/help — Este mensaje"
                        )

            except httpx.TimeoutException:
                continue
            except Exception as exc:
                logger.exception("Bot error: %s", exc)
                await asyncio.sleep(5)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
