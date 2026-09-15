#!/bin/bash
# Consulta de NFe - instalar e executar (Mac/Linux)
# Rodar com: ./executar.sh

cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "[ERRO] Python 3 não encontrado. Instale em https://www.python.org/downloads/"
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "Criando ambiente virtual (primeira vez)..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Instalando dependências..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "Iniciando o aplicativo..."
python main.py
