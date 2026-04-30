"""
Daily BTC portfolio report — sends Telegram summary with current value and P&L.

Usage:
    python -m apps.telegram_bot.daily_btc_report
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from dotenv import load_dotenv

load_dotenv()


def _get_btc_summary() -> dict:
    from binance.client import Client  # type: ignore[import]

    client = Client(
        os.environ["BINANCE_API_KEY"],
        os.environ["BINANCE_SECRET_KEY"],
    )

    account = client.get_account()
    balances = {
        a["asset"]: Decimal(a["free"]) + Decimal(a["locked"])
        for a in account.get("balances", [])
    }
    btc_qty = balances.get("BTC", Decimal("0"))
    eur_cash = balances.get("EUR", Decimal("0"))

    ticker = client.get_symbol_ticker(symbol="BTCEUR")
    current_price = Decimal(ticker["price"])

    # Recalculate avg entry from trade history
    trades = client.get_my_trades(symbol="BTCEUR", limit=500)
    total_eur_spent = Decimal("0")
    total_btc_bought = Decimal("0")
    for t in trades:
        qty = Decimal(t["qty"])
        quote_qty = Decimal(t["quoteQty"])
        if t["isBuyer"]:
            total_eur_spent += quote_qty
            total_btc_bought += qty
        else:
            total_eur_spent -= quote_qty
            total_btc_bought -= qty

    avg_entry = total_eur_spent / total_btc_bought if total_btc_bought > 0 else Decimal("0")
    current_value = btc_qty * current_price
    cost_basis = btc_qty * avg_entry
    pnl_eur = current_value - cost_basis
    pnl_pct = (pnl_eur / cost_basis * 100) if cost_basis > 0 else Decimal("0")

    return {
        "btc_qty": btc_qty,
        "current_price": current_price,
        "current_value": current_value,
        "eur_cash": eur_cash,
        "avg_entry": avg_entry,
        "pnl_eur": pnl_eur,
        "pnl_pct": pnl_pct,
    }


async def main() -> None:
    from apps.telegram_bot.bot import TelegramNotifier

    notifier = TelegramNotifier()
    if not notifier.enabled:
        print("Telegram not configured")
        return

    d = _get_btc_summary()
    pnl_sign = "+" if d["pnl_eur"] >= 0 else ""
    pnl_emoji = "📈" if d["pnl_eur"] >= 0 else "📉"

    msg = (
        f"📊 *Resumen diario — Binance LIVE*\n\n"
        f"₿ BTC: `{d['btc_qty']:.8f}`\n"
        f"💶 Valor actual: `{d['current_value']:.2f} EUR`\n"
        f"💵 EUR en cuenta: `{d['eur_cash']:.2f} EUR`\n\n"
        f"🎯 Precio entrada: `{d['avg_entry']:.2f} EUR`\n"
        f"📌 Precio actual: `{d['current_price']:.2f} EUR`\n\n"
        f"{pnl_emoji} *P&L: {pnl_sign}{d['pnl_eur']:.2f} EUR ({pnl_sign}{d['pnl_pct']:.2f}%)*"
    )

    await notifier.send(msg)
    print("Reporte enviado")


if __name__ == "__main__":
    asyncio.run(main())
