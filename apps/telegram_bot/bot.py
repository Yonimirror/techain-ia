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

    async def send_daily_summary(self, results: list[dict]) -> bool:
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

                            # Próxima ejecución (cada 4h en cierres de vela Binance)
                            candle_closes = [0, 4, 8, 12, 16, 20]
                            next_close = next((h for h in candle_closes if h > now_utc.hour), candle_closes[0])
                            mins_left = ((next_close - now_utc.hour) * 60 - now_utc.minute) % (24 * 60)
                            trading_momento = f"⏰ Próxima revisión: {next_close:02d}:01 UTC (en {mins_left}min)"

                            # Clasificar por mercado
                            CRYPTO  = {'BTC','ETH','SOL','BNB','AVAX','DOT'}
                            ETFS    = {'SPY','QQQ','GLD','TLT','XLF','XLE'}
                            FOREX_S = {'EURUSD=X','GBPUSD=X','USDJPY=X'}
                            FUTURES = {'ES=F','NQ=F','CL=F','GC=F'}

                            def market_label(sym):
                                if sym in CRYPTO:  return "🪙 Crypto"
                                if sym in ETFS:    return "📈 ETF"
                                if sym in FOREX_S: return "💱 Forex"
                                if sym in FUTURES: return "📦 Futuros"
                                return "🏢 Acción"

                            lines = [
                                f"📊 *Estado del sistema* — {hora_utc}",
                                f"{trading_momento}\n",
                                "─────────────────────────",
                            ]

                            seen = set()
                            total_trades = 0
                            best_wr = 0.0
                            best_ret = -999.0
                            by_market: dict[str, list[str]] = {}

                            for row in approved[:15]:
                                h_id = row["hypothesis_id"]
                                sym  = row["symbol"]
                                tf   = row["timeframe"]
                                key  = f"{h_id}_{sym}_{tf}"
                                if key in seen:
                                    continue
                                seen.add(key)

                                mkt = market_label(sym)

                                # Parse strategy name
                                parts  = h_id.split("_")
                                family = parts[0]
                                rsi_p  = next((p.replace("rsi","") for p in parts if p.startswith("rsi") and p[3:].isdigit()), "14")
                                os_v   = next((p.replace("os","") for p in parts if p.startswith("os")), "30")
                                ob_v   = next((p.replace("ob","") for p in parts if p.startswith("ob")), "60")
                                sl_v   = next((p.replace("sl","") for p in parts if p.startswith("sl")), "5")
                                ema_on = "ema200" in h_id
                                tf_txt = "cada 4h" if tf == "4h" else ("cada hora" if tf == "1h" else "diario")
                                ema_txt = " + tendencia EMA200" if ema_on else ""

                                if family == "bollinger":
                                    bb_p = next((p.replace("bb","") for p in parts if p.startswith("bb") and p[2:].isdigit()), "20")
                                    descripcion = (
                                        f"Compra {sym} cuando el precio toca la banda inferior de Bollinger({bb_p}), "
                                        f"vende en banda superior. RSI confirma. Stop {sl_v}%. Revisa {tf_txt}."
                                    )
                                else:
                                    descripcion = (
                                        f"Compra {sym} cuando RSI({rsi_p}) cae bajo {os_v} (pánico), "
                                        f"vende cuando sube sobre {ob_v} (recuperación). "
                                        f"Stop {sl_v}%. Revisa {tf_txt}{ema_txt}."
                                    )

                                saved = load_state(key)
                                if saved:
                                    port    = rebuild_portfolio(saved)
                                    summary = port.summary()
                                    trades  = summary.get("trades", 0)
                                    wr      = summary.get("win_rate", 0.0)
                                    cap     = float(saved.initial_capital)
                                    equity  = float(saved.cash)
                                    ret_pct = (equity - cap) / cap * 100
                                    ks      = saved.risk_state.get("kill_switch_active", False) if saved.risk_state else False
                                    pf      = summary.get("profit_factor", 0.0)

                                    total_trades += trades
                                    if wr > best_wr: best_wr = wr
                                    if ret_pct > best_ret: best_ret = ret_pct

                                    ks_icon  = "🔴 PAUSADA" if ks else "🟢"
                                    wr_icon  = "✅" if wr >= 60 else ("⚠️" if wr >= 45 else "❌")
                                    ret_icon = "📈" if ret_pct >= 0 else "📉"
                                    meta_pct = min(int(trades / 100 * 100), 100)
                                    bar      = '▓' * (meta_pct // 10) + '░' * (10 - meta_pct // 10)

                                    entry = (
                                        f"\n{ks_icon} {mkt} *{sym} {tf}*\n"
                                        f"_{descripcion}_\n\n"
                                        f"{wr_icon} Aciertos: *{wr:.0f}%* de {trades} ops\n"
                                        f"{ret_icon} Retorno: *{ret_pct:+.1f}%* | PF: {pf:.2f} | Sharpe: {row['wf_sharpe_mean']:.2f}\n"
                                        f"🎯 {bar} {trades}/100"
                                    )
                                else:
                                    entry = (
                                        f"\n⚪ {mkt} *{sym} {tf}* — sin datos aún\n"
                                        f"_{descripcion}_\n"
                                        f"Sharpe: {row['wf_sharpe_mean']:.2f}"
                                    )

                                by_market.setdefault(mkt, []).append(entry)

                            # Output por mercado
                            for mkt, entries in by_market.items():
                                lines.append(f"\n{'═'*20}\n{mkt}\n{'═'*20}")
                                lines.extend(entries)

                            lines.append(
                                f"\n─────────────────────────\n"
                                f"📦 Operaciones totales: *{total_trades}*\n"
                                f"🏆 Mejor WR: *{best_wr:.0f}%* | Mejor retorno: *{best_ret:+.1f}%*\n"
                                f"💡 Meta: 100 trades por estrategia → live trading"
                            )

                            # Send in chunks if too long (Telegram limit 4096 chars)
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

                    elif text.startswith("/help"):
                        await notifier.send(
                            "🤖 *Techain-IA Bot*\n\n"
                            "/test — Run on-demand research\n"
                            "/status — Active strategies\n"
                            "/health — Market health check\n"
                            "/help — This message"
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
