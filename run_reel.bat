@echo off
REM ===== Runner para o Auto Reels (Windows) =====
REM - Cria/usa venv
REM - Instala deps (1ª vez)
REM - Roda em loop
REM - Loga tudo em out\runner.log

SETLOCAL ENABLEDELAYEDEXPANSION
set "APPDIR=%~dp0"
cd /d "%APPDIR%"

if not exist out mkdir out

echo [%%date%% %%time%%] Iniciando runner... >> "out\runner.log"

REM === 1) Preparar virtualenv ===
if not exist ".venv\Scripts\python.exe" (
  echo [%%date%% %%time%%] Criando venv... >> "out\runner.log"
  py -3.11 -m venv .venv 2>>"out\runner.log"
)

call ".venv\Scripts\activate.bat"

REM === 2) Garantir dependências (só se faltar) ===
REM Você pode deixar isso comentado depois da 1ª vez:
pip install -r requirements.txt >> "out\runner.log" 2>&1

REM === 3) Loop infinito com auto-restart ===
:loop
echo [%%date%% %%time%%] Iniciando auto_reels_wp_publish.py >> "out\runner.log"
python -u "auto_reels_wp_publish.py" >> "out\runner.log" 2>&1
echo [%%date%% %%time%%] Script saiu com ERRORLEVEL %%errorlevel%%. Reiniciando em 10s... >> "out\runner.log"
timeout /t 10 >nul
goto loop
