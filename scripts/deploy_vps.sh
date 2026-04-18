#!/bin/bash
# =============================================================================
# deploy_vps.sh — Techain-IA: deploy e instalación en VPS Linux
#
# Uso:
#   sudo bash scripts/deploy_vps.sh
#
# Qué hace:
#   1. Instala dependencias del sistema
#   2. Clona o actualiza el repo
#   3. Instala dependencias Python
#   4. Crea los servicios systemd (trader + telegram bot)
#   5. Los arranca y activa en el arranque
# =============================================================================

set -euo pipefail

REPO_URL="https://github.com/Yonimirror/techain-ia.git"
INSTALL_DIR="/opt/techain_ia"
PYTHON="python3"
SERVICE_USER="root"
LOG_DIR="$INSTALL_DIR/logs"
DATA_DIR="$INSTALL_DIR/data"

# ── Colores ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Verificaciones ────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Ejecuta como root: sudo bash scripts/deploy_vps.sh"

info "=== Techain-IA Deploy ==="

# ── 1. Sistema ────────────────────────────────────────────────────────────────
info "Instalando dependencias del sistema..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl

# ── 2. Repo ───────────────────────────────────────────────────────────────────
if [ -d "$INSTALL_DIR/.git" ]; then
    info "Actualizando repo existente..."
    cd "$INSTALL_DIR"
    git pull origin main
else
    info "Clonando repo en $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# ── 3. Entorno Python ─────────────────────────────────────────────────────────
info "Creando entorno virtual..."
$PYTHON -m venv "$INSTALL_DIR/.venv"
source "$INSTALL_DIR/.venv/bin/activate"

info "Instalando dependencias Python..."
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"

# ── 4. Directorios de datos ───────────────────────────────────────────────────
info "Creando directorios..."
mkdir -p "$LOG_DIR" "$DATA_DIR/paper_state" "$DATA_DIR/research/reports" "$DATA_DIR/historical"

# ── 5. Variables de entorno ───────────────────────────────────────────────────
if [ ! -f "$INSTALL_DIR/.env" ]; then
    warn ".env no encontrado — creando plantilla..."
    cat > "$INSTALL_DIR/.env" <<'ENV'
# Binance
BINANCE_API_KEY=
BINANCE_API_SECRET=

# Interactive Brokers
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ENV
    warn "Rellena $INSTALL_DIR/.env antes de arrancar los servicios"
fi

# ── 6. Servicio: paper trader ─────────────────────────────────────────────────
info "Creando servicio systemd: techain-trader..."
cat > /etc/systemd/system/techain-trader.service <<EOF
[Unit]
Description=Techain-IA Paper Trader
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python -m apps.trader_service.main --top 6 --capital 100000
Restart=always
RestartSec=60
StandardOutput=append:$LOG_DIR/paper_trader.log
StandardError=append:$LOG_DIR/paper_trader.log

[Install]
WantedBy=multi-user.target
EOF

# ── 7. Servicio: telegram bot ─────────────────────────────────────────────────
info "Creando servicio systemd: techain-telegram..."
cat > /etc/systemd/system/techain-telegram.service <<EOF
[Unit]
Description=Techain-IA Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python -m apps.telegram_bot.bot
Restart=always
RestartSec=30
StandardOutput=append:$LOG_DIR/telegram_bot.log
StandardError=append:$LOG_DIR/telegram_bot.log

[Install]
WantedBy=multi-user.target
EOF

# ── 8. Activar servicios ──────────────────────────────────────────────────────
info "Activando servicios..."
systemctl daemon-reload
systemctl enable techain-trader techain-telegram
systemctl restart techain-trader techain-telegram

# ── 9. Estado final ───────────────────────────────────────────────────────────
echo ""
info "=== Deploy completado ==="
echo ""
systemctl status techain-trader --no-pager -l | head -15
echo ""
systemctl status techain-telegram --no-pager -l | head -15
echo ""
info "Logs en tiempo real:"
echo "  trader:   journalctl -u techain-trader -f"
echo "  telegram: journalctl -u techain-telegram -f"
echo ""
info "Gestión:"
echo "  systemctl stop    techain-trader"
echo "  systemctl start   techain-trader"
echo "  systemctl restart techain-trader"
