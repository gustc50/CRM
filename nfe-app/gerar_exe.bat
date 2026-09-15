@echo off
REM ============================================================
REM  Gera um .exe standalone (nao precisa de Python instalado
REM  na maquina do cliente depois de pronto).
REM  Rode este arquivo em uma maquina Windows.
REM  O .exe final aparece em: dist\ConsultaNFe.exe
REM ============================================================

cd /d "%~dp0"

if not exist "venv\" (
    python -m venv venv
)
call venv\Scripts\activate.bat

pip install --quiet -r requirements.txt
pip install --quiet pyinstaller

echo Gerando o executavel (pode demorar alguns minutos)...
pyinstaller --onefile --windowed --name "ConsultaNFe" --add-data "gui;gui" main.py

echo.
echo Pronto! O executavel esta em: dist\ConsultaNFe.exe
echo Voce pode copiar so esse arquivo .exe para distribuir aos clientes.
echo.
pause
