"""Porta de entrada para o servidor de produção (gunicorn, waitress, uWSGI).

O servidor embutido do Flask (`python app.py`) serve para testar na sua
máquina, mas não aguenta uso real. Em produção, quem atende as requisições é
um servidor WSGI apontando para este arquivo:

    gunicorn --workers 1 --threads 8 --bind 127.0.0.1:5000 wsgi:app

**Um worker só, com várias threads.** O banco é SQLite: vários processos
gravando no mesmo arquivo dão erro de "database is locked". As threads
resolvem a concorrência de leitura sem esse risco. Além disso, as rotinas de
fundo (consulta ao Asaas, sincronização fiscal) sobem junto com o processo —
com vários workers, elas rodariam duplicadas.

Importar `app` já prepara o banco e as tarefas de fundo, porque `app.py`
chama `iniciar_sistema()` ao ser carregado.
"""
from app import app  # noqa: F401  (é isto que o gunicorn procura)
