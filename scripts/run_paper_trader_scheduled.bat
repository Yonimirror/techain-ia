@echo off
cd /d c:\proyectos\techain_ia
python -m apps.trader_service.main --capital 1000000 --top 13 >> logs/paper_trader.log 2>&1
