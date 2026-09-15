@echo off
setlocal enabledelayedexpansion
title Consulta de NFe - instalando e executando
cd /d "%~dp0"

echo ============================================================
echo  Consulta de NFe
echo ============================================================
echo Pasta atual: %cd%
echo.

REM --- Aviso se estiver rodando de dentro do .zip (pasta somente-leitura) ---
REM Procura a assinatura real da pasta de pre-visualizacao do Windows, e nao
REM a palavra "Temp" solta: caminhos legitimos como "Documentos\Templates"
REM davam falso positivo e bloqueavam a execucao.
REM (os padroes nao terminam em "\" de proposito: uma barra invertida logo
REM antes da aspa final vira aspa escapada e quebra o argumento do findstr)
set "RODANDO_NO_ZIP="
echo "%cd%" | findstr /I /C:"\AppData\Local\Temp" >nul
if not errorlevel 1 set "RODANDO_NO_ZIP=1"
echo "%cd%" | findstr /I /C:".zip" >nul
if not errorlevel 1 set "RODANDO_NO_ZIP=1"
if defined RODANDO_NO_ZIP (
    echo [AVISO] Parece que voce esta rodando direto de dentro do arquivo .zip.
    echo Isso NAO funciona - o Windows abre o zip como pasta temporaria e somente-leitura.
    echo.
    echo SOLUCAO: clique com o botao direito no nfe-app.zip, escolha
    echo "Extrair tudo..." primeiro, abra a pasta extraida, e so entao
    echo de duplo-clique no executar.bat de dentro dela.
    echo.
    pause
    exit /b 1
)

REM --- Verifica Python (tenta "python" e, se falhar, o launcher "py") ---
echo Verificando instalacao do Python...
set "PY="
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"

if not defined PY (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)

if not defined PY (
    echo.
    echo [ERRO] Python nao foi encontrado neste computador.
    echo.
    echo Baixe e instale em: https://www.python.org/downloads/
    echo IMPORTANTE: na tela de instalacao, marque a caixa
    echo "Add python.exe to PATH" antes de clicar em Install.
    echo.
    echo Depois de instalar, feche esta janela e de duplo-clique
    echo neste arquivo de novo.
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('%PY% --version') do echo Encontrado: %%v ^(comando: %PY%^)
echo.

REM --- Cria ambiente virtual se nao existir ---
if not exist "venv\Scripts\activate.bat" (
    echo Criando ambiente virtual Python ^(so na primeira vez^)...
    %PY% -m venv venv
    if errorlevel 1 (
        echo.
        echo [ERRO] Falha ao criar o ambiente virtual.
        echo Confirme que voce extraiu o .zip para uma pasta normal
        echo ^(ex: Area de Trabalho, Documentos^) e nao esta rodando
        echo de dentro do zip ou de uma pasta protegida.
        echo.
        pause
        exit /b 1
    )
)

echo Ativando ambiente virtual...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERRO] Nao foi possivel ativar o ambiente virtual.
    pause
    exit /b 1
)

echo Instalando dependencias ^(pode demorar na primeira vez^)...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao instalar as dependencias. Veja a mensagem
    echo de erro acima - geralmente e falta de conexao com a internet.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Iniciando o aplicativo...
echo  ^(abre uma JANELA PROPRIA do programa - nao e no navegador^)
echo ============================================================
python main.py
REM Guarda o codigo AGORA: os echos abaixo rodam antes do teste, e dentro de
REM um bloco if a variavel errorlevel e expandida na leitura do bloco. Sem
REM isso o aviso saia com o codigo errado - justamente o numero que o
REM usuario copia pra pedir ajuda.
set "CODIGO_SAIDA=%errorlevel%"

echo.
echo O aplicativo foi fechado.
if not "%CODIGO_SAIDA%"=="0" (
    echo [AVISO] O programa terminou com erro ^(codigo %CODIGO_SAIDA%^).
    echo Copie a mensagem acima se for pedir ajuda.
)
echo.
pause
