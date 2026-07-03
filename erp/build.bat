@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo   ERP Financeiro - Build do executavel (.exe)
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao foi encontrado no PATH.
    echo Instale o Python 3.10+ em https://www.python.org/downloads/
    echo IMPORTANTE: marque a opcao "Add python.exe to PATH" durante a instalacao.
    pause
    exit /b 1
)

if not exist venv (
    echo Criando ambiente virtual em .\venv ...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo.
echo Instalando dependencias...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
pip install pyinstaller

if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo.
echo Gerando o executavel com PyInstaller...
pyinstaller --noconfirm --onefile ^
    --name "ERP-Financeiro" ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    app.py

if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao gerar o executavel. Veja as mensagens acima.
    pause
    exit /b 1
)

echo.
echo ============================================
echo Pronto! O executavel esta em: dist\ERP-Financeiro.exe
echo Copie esse arquivo .exe para onde quiser e rode-o
echo com um duplo clique. O banco de dados (erp.db) sera
echo criado automaticamente ao lado do .exe.
echo ============================================
pause
