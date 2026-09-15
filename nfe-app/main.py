"""
Ponte entre a interface (HTML/JS) e a lógica Python.
Toda função pública aqui vira "window.pywebview.api.<nome>()" no JS.
"""
import os
import sys
import webview
import db
import certificado
import sefaz_client


def _webview2_disponivel() -> bool:
    """Sem o WebView2 Runtime, o pywebview cai pro motor antigo do IE sem avisar e a janela fica em branco."""
    if sys.platform != "win32":
        return True

    import winreg

    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # Edge WebView2 Runtime
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for subkey in (
            rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}",
            rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}",
        ):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    winreg.QueryValueEx(key, "pv")
                    return True
            except OSError:
                continue
    return False


def _avisar_webview2_ausente():
    mensagem = (
        "O Microsoft Edge WebView2 Runtime nao foi encontrado neste "
        "computador.\n\n"
        "Ele e necessario para exibir a interface do app (sem ele a janela "
        "abre em branco/quebrada).\n\n"
        "Baixe e instale (gratuito, cerca de 1 minuto) em:\n"
        "https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n"
        "Depois de instalar, feche esta janela e execute o executar.bat de novo."
    )
    print(mensagem)
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, mensagem, "Consulta de NFe", 0x10)
    except Exception:
        pass


class Api:

    def __init__(self):
        # Senhas de certificado ficam só em memória (RAM), nunca em disco.
        # Somem quando o app é fechado. Evita pedir a senha de novo a cada
        # consulta na mesma sessão.
        self._senhas_sessao = {}

    # ---------------- Empresas / Clientes ----------------

    def listar_empresas(self):
        return db.listar_empresas()

    def escolher_arquivo_certificado(self):
        """Abre o diálogo nativo do SO pra escolher o .pfx do cliente."""
        janela = webview.windows[0]
        resultado = janela.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=("Certificado digital (*.pfx;*.p12)", "Todos os arquivos (*.*)"),
        )
        if not resultado:
            return {"ok": False}
        return {"ok": True, "path": resultado[0]}

    def validar_certificado(self, pfx_path, senha):
        try:
            info = certificado.validar_pfx_rapido(pfx_path, senha)
            return {"ok": True, **info}
        except certificado.CertificadoInvalido as e:
            return {"ok": False, "erro": str(e)}
        except Exception as e:
            return {"ok": False, "erro": f"Erro inesperado: {e}"}

    def cadastrar_empresa(self, razao_social, cnpj, certificado_path, ambiente, uf_autor, senha_certificado=None):
        cnpj_limpo = "".join(ch for ch in cnpj if ch.isdigit())
        empresa_id = db.criar_empresa(razao_social, cnpj_limpo, certificado_path, ambiente, uf_autor)
        if senha_certificado:
            self._senhas_sessao[empresa_id] = senha_certificado
        return {"ok": True, "empresa_id": empresa_id}

    def excluir_empresa(self, empresa_id):
        db.excluir_empresa(empresa_id)
        self._senhas_sessao.pop(empresa_id, None)
        return {"ok": True}

    # ---------------- Consulta / Download de notas ----------------

    def _minutos_de_espera(self, empresa_id):
        """Retorna quantos minutos faltam pra poder consultar de novo
        (0 = liberado). Baseado na última resposta 137/656 registrada no log.
        Espera 1h + 3 min de margem: se o relógio do PC estiver adiantado em
        relação ao da SEFAZ, tentar na hora exata rende outro 656 e o
        bloqueio recomeça — a margem evita esse ciclo."""
        from datetime import datetime, timedelta

        ultima = db.ultima_consulta(empresa_id)
        if not ultima or ultima["cstat"] not in ("137", "656"):
            return 0
        try:
            anterior = datetime.fromisoformat(ultima["data_hora"])
        except (ValueError, TypeError):
            return 0
        falta = timedelta(minutes=63) - (datetime.now() - anterior)
        segundos = falta.total_seconds()
        return int(segundos // 60) + 1 if segundos > 0 else 0

    def resincronizar(self, empresa_id):
        """Zera o cursor NSU: a próxima busca re-baixa tudo que a SEFAZ ainda
        guarda (~90 dias). Não apaga nem duplica notas já salvas. Usar quando
        uma nota conhecida não apareceu (o Ambiente Nacional às vezes responde
        '137 - nada' indevidamente e o cursor pula documentos).

        O cursor zerado fica valendo para a próxima busca PERMITIDA — a
        re-sincronização não dispara consulta forçada. Forçar aqui era o que
        prendia o usuário: quem re-sincroniza acabou de receber um 137, então
        a janela de 1h está sempre correndo; a SEFAZ respondia 656 (consumo
        indevido), o 656 reiniciava o bloqueio, e cada nova tentativa de
        re-sincronizar renovava a espera sem nunca baixar nada."""
        db.resetar_nsu(empresa_id)
        return {"ok": True, "minutos_espera": self._minutos_de_espera(empresa_id)}

    def estado_consulta(self, empresa_id):
        """Chamado pela interface ao abrir uma empresa: diz se o botão de
        busca está liberado ou quantos minutos faltam (regra de 1h da SEFAZ).
        O contador é POR CNPJ — cada empresa tem o seu, independente."""
        ultima = db.ultima_consulta(empresa_id)
        return {
            "minutos_espera": self._minutos_de_espera(empresa_id),
            "ultimo_cstat": ultima["cstat"] if ultima else None,
            "ultima_hora": ultima["data_hora"] if ultima else None,
            # Se a senha já está na sessão, a interface pode re-consultar
            # sozinha quando o contador zerar (rotina de 1x/hora).
            "senha_em_cache": empresa_id in self._senhas_sessao,
        }

    def consultar_notas(self, empresa_id, senha_certificado=None, forcar=False):
        """
        Faz UM lote de consulta distNSU pra empresa. Pode ser chamado
        várias vezes seguidas pelo frontend (com um pequeno delay) até
        o cStat voltar 137 (sem novidades).

        A senha só precisa ser digitada uma vez por sessão do app: fica
        guardada em memória (nunca em disco) e é reaproveitada nas consultas
        seguintes até o app ser fechado.

        forcar=True pula a checagem local de 1 hora (por conta e risco do
        usuário — se a SEFAZ ainda considerar cedo, responde 656).
        """
        empresa = db.obter_empresa(empresa_id)
        if not empresa:
            return {"ok": False, "erro": "Empresa não encontrada."}

        # Regra da SEFAZ: depois de um 137 (nada novo) ou 656 (consumo
        # indevido), o mesmo CNPJ só pode consultar de novo após 1 hora.
        # Bloqueia aqui pra não gastar (nem estender) o bloqueio lá.
        if not forcar:
            bloqueio = self._minutos_de_espera(empresa_id)
            if bloqueio > 0:
                return {
                    "ok": False,
                    "aguardar": True,
                    "erro": (
                        f"A SEFAZ exige intervalo de 1 hora entre consultas sem novidade. "
                        f"Aguarde ~{bloqueio} min e tente de novo — as notas já baixadas "
                        f"continuam disponíveis na lista abaixo."
                    ),
                }

        if senha_certificado:
            self._senhas_sessao[empresa_id] = senha_certificado
        else:
            senha_certificado = self._senhas_sessao.get(empresa_id)

        if not senha_certificado:
            return {"ok": False, "senha_necessaria": True, "erro": "Digite a senha do certificado."}

        try:
            with certificado.CertificadoContext(empresa["certificado_path"], senha_certificado) as ctx:
                if ctx.cnpj and ctx.cnpj != empresa["cnpj"]:
                    return {
                        "ok": False,
                        "erro": (
                            f"O certificado pertence ao CNPJ {ctx.cnpj}, "
                            f"diferente do CNPJ cadastrado ({empresa['cnpj']})."
                        ),
                    }

                resultado = sefaz_client.consultar_distnsu(
                    cnpj=empresa["cnpj"],
                    uf_autor=empresa["uf_autor"],
                    ambiente=empresa["ambiente"],
                    ult_nsu=empresa["ultimo_nsu"],
                    cert_pair=ctx.cert_pair,
                )
        except certificado.CertificadoInvalido as e:
            # Senha errada ou certificado ilegível: descarta o cache pra
            # forçar nova digitação, em vez de ficar tentando com senha ruim.
            self._senhas_sessao.pop(empresa_id, None)
            return {"ok": False, "senha_necessaria": True, "erro": str(e)}
        except sefaz_client.ErroSefaz as e:
            db.registrar_log(empresa_id, e.cstat, e.xmotivo, 0)
            if e.cstat == "656":
                return {
                    "ok": False,
                    "aguardar": True,
                    "erro": (
                        "A SEFAZ bloqueou temporariamente novas consultas deste CNPJ "
                        "(consumo indevido — consultas repetidas em menos de 1 hora). "
                        "O bloqueio expira sozinho; o app vai liberar o botão em ~1 hora."
                    ),
                }
            return {"ok": False, "erro": f"SEFAZ rejeitou: [{e.cstat}] {e.xmotivo}"}
        except Exception as e:
            return {"ok": False, "erro": f"Erro de comunicação com a SEFAZ: {e}"}

        novos = 0
        eventos = 0
        outros = 0
        for doc in resultado["documentos"]:
            if doc["tipo"] == "resNFe":
                dados = sefaz_client.extrair_dados_resumo(doc["xml"])
                db.salvar_nota(
                    empresa_id=empresa_id,
                    chave_nfe=dados["chave_nfe"],
                    nsu=doc["nsu"],
                    tipo_doc="resNFe",
                    emitente_cnpj=dados["emitente_cnpj"],
                    emitente_nome=dados["emitente_nome"],
                    valor_total=dados["valor_total"],
                    data_emissao=dados["data_emissao"],
                    situacao=dados["situacao"],
                    xml_completo=doc["xml"],
                )
                novos += 1
            elif doc["tipo"] == "nfeProc":
                # NFe completa (quando disponível) — sobrescreve o resumo da mesma chave
                dados = sefaz_client.extrair_dados_nfeproc(doc["xml"])
                db.salvar_nota(
                    empresa_id=empresa_id,
                    chave_nfe=dados["chave_nfe"],
                    nsu=doc["nsu"],
                    tipo_doc="nfeProc",
                    emitente_cnpj=dados["emitente_cnpj"],
                    emitente_nome=dados["emitente_nome"],
                    valor_total=dados["valor_total"],
                    data_emissao=dados["data_emissao"],
                    situacao=dados["situacao"],
                    xml_completo=doc["xml"],
                )
                novos += 1
            elif doc["tipo"] == "resEvento":
                # Evento (cancelamento, carta de correção...) — não é nota,
                # mas contamos pra interface mostrar o que a SEFAZ enviou.
                eventos += 1
            else:
                outros += 1

        db.atualizar_ultimo_nsu(empresa_id, resultado["ult_nsu"])
        db.registrar_log(empresa_id, resultado["cstat"], resultado["xmotivo"], novos)

        # Assinatura de possível perda: a SEFAZ respondeu "nada encontrado"
        # mas AVANÇOU o cursor por cima de números que nunca nos entregou.
        # (Instabilidade conhecida do Ambiente Nacional, sobretudo na
        # primeira consulta de um CNPJ.)
        nsu_pulados = 0
        if resultado["cstat"] == "137":
            try:
                nsu_pulados = max(0, int(resultado["ult_nsu"]) - int(empresa["ultimo_nsu"]))
            except (ValueError, TypeError):
                nsu_pulados = 0

        return {
            "ok": True,
            "cstat": resultado["cstat"],
            "xmotivo": resultado["xmotivo"],
            "novos_documentos": novos,
            "eventos": eventos,
            "outros": outros,
            "ult_nsu": resultado["ult_nsu"],
            "max_nsu": resultado["max_nsu"],
            "nsu_pulados": nsu_pulados,
            "tem_mais": resultado["ult_nsu"] != resultado["max_nsu"] and resultado["cstat"] == "138",
        }

    def listar_notas(self, empresa_id, data_inicio=None, data_fim=None):
        return db.listar_notas(empresa_id, data_inicio, data_fim)

    def baixar_xml(self, nota_id):
        """Abre diálogo de 'salvar como' e grava o XML da nota em disco."""
        nota = db.obter_nota_xml(nota_id)
        if not nota:
            return {"ok": False, "erro": "Nota não encontrada."}

        janela = webview.windows[0]
        destino = janela.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=f"{nota['chave_nfe']}.xml",
        )
        if not destino:
            return {"ok": False}

        caminho = destino if isinstance(destino, str) else destino[0]
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(nota["xml_completo"])

        return {"ok": True, "path": caminho}

    def baixar_xmls(self, nota_ids):
        """Abre diálogo de escolher PASTA e salva o XML de cada nota
        selecionada nela (um arquivo por nota, nomeado pela chave)."""
        if not nota_ids:
            return {"ok": False, "erro": "Nenhuma nota selecionada."}

        janela = webview.windows[0]
        destino = janela.create_file_dialog(webview.FOLDER_DIALOG)
        if not destino:
            return {"ok": False}
        pasta = destino if isinstance(destino, str) else destino[0]

        salvos = _salvar_xmls_em(pasta, nota_ids)
        return {"ok": True, "salvos": salvos, "pasta": pasta}


def _salvar_xmls_em(pasta, nota_ids):
    """Grava os XMLs das notas na pasta indicada. Retorna quantos salvou."""
    salvos = 0
    nomes_usados = set()
    for nota_id in nota_ids:
        nota = db.obter_nota_xml(nota_id)
        if not nota or not nota.get("xml_completo"):
            continue
        nome_base = nota["chave_nfe"] or f"nota_{nota['id']}"
        nome = f"{nome_base}.xml"
        if nome in nomes_usados:
            nome = f"{nome_base}_{nota['id']}.xml"
        nomes_usados.add(nome)
        with open(os.path.join(pasta, nome), "w", encoding="utf-8") as f:
            f.write(nota["xml_completo"])
        salvos += 1
    return salvos


def main():
    if not _webview2_disponivel():
        _avisar_webview2_ausente()
        sys.exit(1)

    db.init_db()
    api = Api()
    # Quando empacotado com PyInstaller (--onefile), os arquivos estáticos
    # são extraídos em sys._MEIPASS, não na pasta de __file__.
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
    gui_path = os.path.join(base_dir, "gui", "index.html")
    webview.create_window(
        "Consulta de NFe — Distribuição DFe",
        gui_path,
        js_api=api,
        width=1100,
        height=750,
        min_size=(900, 600),
    )
    webview.start(debug=False, gui="edgechromium" if sys.platform == "win32" else None)


if __name__ == "__main__":
    main()
