#!/usr/bin/env bash
# Sobe o sistema em modo servidor (Linux). Uso: ./iniciar.sh
#
# Faz o feijão com arroz: cria o ambiente virtual se não existir, instala as
# dependências e entrega o serviço ao gunicorn. Para rodar sozinho depois de
# reiniciar a máquina, veja a seção de instalação no README (systemd).
set -euo pipefail

cd "$(dirname "$0")"

PORTA="${ERP_PORT:-5000}"
ENDERECO="${ERP_BIND:-127.0.0.1:$PORTA}"

if [ ! -d venv ]; then
    echo "Criando o ambiente virtual em ./venv ..."
    python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo "Instalando dependências..."
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

echo
echo "Servindo em http://$ENDERECO"
echo "Coloque um proxy com HTTPS na frente antes de abrir para a internet."
echo

# 1 worker: o banco é SQLite (ver comentário em wsgi.py).
exec gunicorn \
    --workers 1 \
    --threads 8 \
    --timeout 120 \
    --bind "$ENDERECO" \
    --access-logfile - \
    wsgi:app
