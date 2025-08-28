@echo off
cd /d %~dp0
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate
pip install --no-cache-dir -r requirements.txt
py boca_app.py
echo.
echo (Pressione qualquer tecla para fechar...)
pause >nul
