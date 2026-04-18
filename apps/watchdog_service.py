#!/usr/bin/env python
"""
Watchdog Service — Mantiene trader y dashboard corriendo.

Monitorea cada 5 minutos:
1. Si el trader no ha ejecutado en las últimas 25 horas → ejecuta
2. Si el dashboard no está corriendo → reinicia
3. Log de todos los eventos para debugging

Ejecutar: python apps/watchdog_service.py
"""
import os
import subprocess
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
import psutil
import json

# Configurar logging
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / "watchdog.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Derive TRADER_DIR from this file's location so it works on any machine
TRADER_DIR = Path(__file__).resolve().parent.parent
PAPER_STATE_DIR = TRADER_DIR / "data" / "paper_state"
CHECK_INTERVAL = int(os.environ.get("WATCHDOG_CHECK_INTERVAL", "300"))    # default 5m
TRADER_MAX_AGE_HOURS = int(os.environ.get("WATCHDOG_MAX_AGE_HOURS", "25"))
TRADER_CAPITAL = os.environ.get("TRADER_CAPITAL", "1000000")
TRADER_TOP = os.environ.get("TRADER_TOP", "16")


def _get_latest_state_timestamp():
    """Obtiene el timestamp del archivo de estado más reciente."""
    if not PAPER_STATE_DIR.exists():
        return None

    json_files = list(PAPER_STATE_DIR.glob("*.json"))
    if not json_files:
        return None

    latest = max(json_files, key=lambda x: x.stat().st_mtime)
    mtime = latest.stat().st_mtime
    return datetime.fromtimestamp(mtime)


def _is_trader_running():
    """Verifica si hay un proceso Python corriendo trader."""
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline and 'trader_service' in ' '.join(cmdline):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def _is_dashboard_running():
    """Verifica si Streamlit está corriendo."""
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline and 'streamlit' in ' '.join(cmdline):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def _start_trader():
    """Inicia el paper trader."""
    logger.info("[START] Iniciando paper trader...")
    try:
        subprocess.Popen(
            [
                "python",
                "-m",
                "apps.trader_service.main",
                "--capital", TRADER_CAPITAL,
                "--top", TRADER_TOP,
            ],
            cwd=TRADER_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[OK] Paper trader iniciado")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Fallo al iniciar trader: {e}")
        return False


def _start_dashboard():
    """Inicia el dashboard Streamlit."""
    logger.info("[START] Iniciando dashboard...")
    try:
        subprocess.Popen(
            [
                "streamlit",
                "run",
                "apps/dashboard/app.py",
                "--server.port", "8501",
                "--logger.level", "error",
            ],
            cwd=TRADER_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[OK] Dashboard iniciado")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Fallo al iniciar dashboard: {e}")
        return False


def _check_and_restart():
    """Chequea procesos y reinicia si es necesario."""
    latest_state = _get_latest_state_timestamp()
    trader_running = _is_trader_running()
    dashboard_running = _is_dashboard_running()

    logger.info(f"[CHECK] Trader={trader_running} | Dashboard={dashboard_running}")

    # Checar si el trader ejecutó recientemente
    if latest_state:
        age = datetime.now() - latest_state
        hours_ago = age.total_seconds() / 3600
        logger.info(f"[INFO] Ultimo estado: hace {hours_ago:.1f} horas")

        if hours_ago > TRADER_MAX_AGE_HOURS:
            logger.warning(f"[WARN] Trader no ejecuto en {hours_ago:.1f}h > {TRADER_MAX_AGE_HOURS}h")
            if not trader_running:
                logger.info("[RESTART] Reiniciando trader (no ejecuto en tiempo esperado)")
                _start_trader()
                time.sleep(5)
    else:
        logger.warning("[WARN] No hay archivos de estado. Primera ejecucion?")
        if not trader_running:
            _start_trader()

    # Reiniciar dashboard si no está corriendo
    if not dashboard_running:
        logger.warning("[WARN] Dashboard no esta corriendo")
        _start_dashboard()
        time.sleep(5)

    # Reiniciar trader si no está corriendo (pero solo si debería estar)
    if not trader_running and latest_state and (datetime.now() - latest_state).total_seconds() / 3600 < TRADER_MAX_AGE_HOURS * 2:
        logger.warning("[WARN] Trader se detuvo inesperadamente")
        _start_trader()


def main():
    """Loop principal del watchdog."""
    logger.info("=" * 60)
    logger.info("WATCHDOG SERVICE INICIADO")
    logger.info(f"Intervalo de check: {CHECK_INTERVAL}s ({CHECK_INTERVAL/60:.0f} min)")
    logger.info(f"Max edad trader: {TRADER_MAX_AGE_HOURS}h")
    logger.info("=" * 60)

    try:
        while True:
            try:
                _check_and_restart()
            except Exception as e:
                logger.error(f"Error en check: {e}")

            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        logger.info("STOP - Watchdog detenido por usuario")


if __name__ == "__main__":
    main()
