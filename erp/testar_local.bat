@echo off
setlocal

rem ============================================================
rem  Gestao Financeira - teste local (Windows)
rem
rem  Sobe o sistema na sua maquina para voce testar no navegador.
rem  E o servidor de desenvolvimento do Flask: serve para testar,
rem  NAO para colocar no ar. Para o servidor de verdade, veja a
rem  secao "Como colocar no ar" no README.
rem
rem  Uso:
rem     testar_local.bat            sobe o sistema
rem     testar_local.bat limpar     apaga o banco e comeca do zero
rem ============================================================

cd /d "%~dp0"

rem So na sua maquina: nada de escutar na rede durante o teste.
set "ERP_HOST=127.0.0.1"
if not defined ERP_PORT set "ERP_PORT=5000"
set "ENDERECO=http://127.0.0.1:%ERP_PORT%"

echo ============================================================
echo   Gestao Financeira - teste local
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Python nao foi encontrado no PATH.
    echo Instale o Python 3.10+ em https://www.python.org/downloads/
    echo IMPORTANTE: marque "Add python.exe to PATH" durante a instalacao.
    echo.
    pause
    exit /b 1
)

rem ------------------------------------------------------------
rem  Apagar o banco, se pedido
rem ------------------------------------------------------------
if /i not "%~1"=="limpar" goto :depois_da_limpeza

if not exist erp.db (
    echo Nao existe erp.db ainda - nada para apagar.
    echo.
    goto :depois_da_limpeza
)

echo Isso apaga TODOS os dados que voce lancou no teste.
set "CONFIRMA="
set /p CONFIRMA="Digite SIM para confirmar: "
if /i not "%CONFIRMA%"=="SIM" (
    echo Cancelado. Nada foi apagado.
    echo.
    pause
    exit /b 0
)
del /q erp.db
if exist .chave_sessao del /q .chave_sessao
echo Banco apagado. As contas de teste serao criadas de novo.
echo.

:depois_da_limpeza

rem ------------------------------------------------------------
rem  Ambiente virtual e dependencias
rem ------------------------------------------------------------
if not exist venv (
    echo Criando o ambiente virtual em .\venv ...
    python -m venv venv
    if errorlevel 1 (
        echo [ERRO] Falha ao criar o ambiente virtual.
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat

echo Instalando as dependencias ^(demora so na primeira vez^)...
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERRO] Falha ao instalar as dependencias. Veja as mensagens acima.
    pause
    exit /b 1
)

rem ------------------------------------------------------------
rem  Sobe o servidor
rem ------------------------------------------------------------
echo.
echo ============================================================
echo   Servidor em %ENDERECO%
echo.
echo   Contas de teste ^(criadas so quando o banco esta vazio^):
echo     admin      admin@teste.com.br       admin-teste-123
echo     cliente    cliente1@teste.com.br    cliente-teste-123
echo     cliente    cliente2@teste.com.br    cliente-teste-123  ^(vencido^)
echo     contador   contador@teste.com.br    contador-teste-123
echo.
echo   O navegador abre sozinho em alguns segundos.
echo   Para parar: Ctrl+C, ou feche esta janela.
echo ============================================================
echo.

rem Abre o navegador so depois que o servidor tiver subido.
start "" /min cmd /c "ping -n 6 127.0.0.1 >nul & start %ENDERECO%"

python app.py

echo.
echo Servidor encerrado.
pause
