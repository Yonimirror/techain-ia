@echo off
cd /d c:\proyectos\techain_ia
C:\Users\josea\AppData\Local\Python\pythoncore-3.14-64\python.exe -m apps.trader_service.main --top 6 --capital 100000 >> data\logs\paper_trader.log 2>&1
