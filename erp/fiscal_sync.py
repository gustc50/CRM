"""Sincronização dos documentos fiscais (NFS-e e NF-e) com o ERP.

Executa as buscas em segundo plano (thread), grava as notas nas tabelas do
banco do ERP e mantém o controle incremental por NSU nas configurações.
Portado dos motores de sincronização do "NFS-e Monitor" e do "Consulta de NFe".
"""
from __future__ import annotations

import datetime as dt
import gzip
import os
import re
import threading
import time

from models import (
    Configuracao,
    NotaEletronica,
    NotaServico,
    NotaServicoEvento,
    db,
)
import fiscal_certificado
import fiscal_nfe
import fiscal_nfse

# Pausa entre chamadas consecutivas de distribuição (educação com os servidores)
PAUSA_ENTRE_LOTES = 1.0

# Máximo de lotes por sincronização de NF-e (mesma proteção do app original)
NFE_MAX_LOTES = 10

# A SEFAZ exige 1h de intervalo após um 137/656; 3 min de margem para
# relógios adiantados (o mesmo racional do app original).
NFE_ESPERA_MINUTOS = 63


def _agora() -> str:
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _parse_data(valor: str | None):
    if not valor:
        return None
    try:
        return dt.datetime.strptime(str(valor)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _digitos(valor: str | None) -> str:
    return re.sub(r'\D', '', valor or '')


# ------------------------------------------------------------------ #
# Configurações (chave/valor no banco)
# ------------------------------------------------------------------ #

def config_get(chave: str, padrao: str = '') -> str:
    linha = Configuracao.query.filter_by(chave=chave).first()
    return linha.valor if linha and linha.valor is not None else padrao


def config_set(chave: str, valor: str | None) -> None:
    linha = Configuracao.query.filter_by(chave=chave).first()
    if linha is None:
        linha = Configuracao(chave=chave)
        db.session.add(linha)
    linha.valor = valor
    db.session.commit()


def certificado_configurado() -> bool:
    return bool(config_get('cert_caminho') and config_get('cert_senha_cripto'))


# ------------------------------------------------------------------ #
# Classificação de papel (emitida = a receber / recebida = a pagar)
# ------------------------------------------------------------------ #

def classificar_papel_nfse(doc_empresa: str, nota: fiscal_nfse.NotaExtraida) -> str:
    doc = doc_empresa or ''
    raiz = doc[:8]
    if nota.prestador_doc == doc:
        return 'emitida'
    if nota.tomador_doc == doc:
        return 'recebida'
    # Certificado pode ser da matriz consultando notas de filiais (CNPJ raiz)
    if raiz and nota.prestador_doc.startswith(raiz):
        return 'emitida'
    if raiz and nota.tomador_doc.startswith(raiz):
        return 'recebida'
    return 'recebida'


def classificar_papel_nfe(doc_empresa: str, emitente_doc: str | None) -> str:
    doc = _digitos(doc_empresa)
    emitente = _digitos(emitente_doc)
    if doc and emitente and (emitente == doc or (doc[:8] and emitente.startswith(doc[:8]))):
        return 'emitida'
    return 'recebida'


# ------------------------------------------------------------------ #
# Gerenciador de sincronização
# ------------------------------------------------------------------ #

class SyncFiscal:
    """Executa e acompanha as sincronizações de NFS-e e NF-e (uma por vez cada)."""

    def __init__(self, app, caminho_chave_fernet: str, ca_extra_path: str):
        self.app = app
        self.caminho_chave = caminho_chave_fernet
        self.ca_extra_path = ca_extra_path
        self._lock = threading.Lock()
        self._status: dict[str, dict] = {}

    # -------------------------- infra ------------------------------- #

    def status(self, qual: str) -> dict:
        with self._lock:
            return dict(self._status.get(qual, {'estado': 'ocioso'}))

    def em_execucao(self, qual: str) -> bool:
        return self.status(qual).get('estado') == 'executando'

    def _atualizar(self, qual: str, **campos) -> None:
        with self._lock:
            self._status.setdefault(qual, {}).update(campos)

    def _verify(self):
        """Bundle de CA extra (cadeia ICP-Brasil ou CA de testes), se existir."""
        if os.path.exists(self.ca_extra_path):
            return self.ca_extra_path
        return True

    def _credenciais(self):
        caminho = config_get('cert_caminho')
        senha_cripto = config_get('cert_senha_cripto')
        if not caminho or not senha_cripto:
            raise fiscal_certificado.CertificadoInvalido(
                'Certificado digital não configurado. Informe o caminho e a senha na aba Configurações.'
            )
        senha = fiscal_certificado.descriptografar_senha(senha_cripto, self.caminho_chave)
        return caminho, senha

    def iniciar(self, qual: str) -> bool:
        if qual not in ('nfse', 'nfe'):
            return False
        with self._lock:
            if self._status.get(qual, {}).get('estado') == 'executando':
                return False
            self._status[qual] = {
                'estado': 'executando',
                'mensagem': 'Conectando...',
                'novos': 0,
                'iniciado_em': _agora(),
            }
        alvo = self._executar_nfse if qual == 'nfse' else self._executar_nfe
        threading.Thread(target=alvo, daemon=True).start()
        return True

    # -------------------------- NFS-e ------------------------------- #

    def _executar_nfse(self) -> None:
        with self.app.app_context():
            try:
                caminho, senha = self._credenciais()
                doc_empresa = _digitos(config_get('cert_documento'))
                ambiente = config_get('nfse_ambiente', 'producao')
                ultimo_nsu = int(config_get('nfse_ultimo_nsu', '0') or 0)

                notas_novas = 0
                eventos_novos = 0

                with fiscal_certificado.CertificadoContext(caminho, senha) as ctx:
                    cliente = fiscal_nfse.AdnClient(
                        ambiente,
                        ctx.cert_pair,
                        base_adn=os.environ.get('ERP_ADN_BASE'),
                        base_sefin=os.environ.get('ERP_SEFIN_BASE'),
                        verify=self._verify(),
                    )
                    with cliente:
                        while True:
                            self._atualizar('nfse', mensagem=f'Consultando documentos a partir do NSU {ultimo_nsu}...')
                            resposta = cliente.distribuir_dfe(ultimo_nsu)
                            if not resposta.documentos:
                                break

                            n_notas, n_eventos = self._gravar_lote_nfse(doc_empresa, resposta.documentos)
                            notas_novas += n_notas
                            eventos_novos += n_eventos

                            maior_nsu = max(d.nsu for d in resposta.documentos)
                            if resposta.ult_nsu is not None:
                                maior_nsu = max(maior_nsu, resposta.ult_nsu)
                            if maior_nsu <= ultimo_nsu:
                                break  # proteção contra loop sem avanço
                            ultimo_nsu = maior_nsu
                            config_set('nfse_ultimo_nsu', str(ultimo_nsu))
                            self._atualizar('nfse', novos=notas_novas)

                            if not resposta.tem_mais:
                                break
                            time.sleep(PAUSA_ENTRE_LOTES)

                self._aplicar_eventos_nfse()
                config_set('nfse_ultimo_nsu', str(ultimo_nsu))
                config_set('nfse_ultima_sync', _agora())

                mensagem = (
                    f'Sincronização concluída: {notas_novas} nota(s) e '
                    f'{eventos_novos} evento(s) novos. Último NSU: {ultimo_nsu}.'
                )
                config_set('nfse_ultimo_status', mensagem)
                self._atualizar('nfse', estado='concluido', mensagem=mensagem, novos=notas_novas)
            except (fiscal_nfse.AdnErro, fiscal_certificado.CertificadoInvalido) as exc:
                config_set('nfse_ultimo_status', f'Erro: {exc}')
                self._atualizar('nfse', estado='erro', mensagem=str(exc))
            except Exception as exc:  # superfície única de erro para a UI
                config_set('nfse_ultimo_status', f'Erro inesperado: {exc}')
                self._atualizar('nfse', estado='erro', mensagem=f'Erro inesperado na sincronização: {exc}')

    def _gravar_lote_nfse(self, doc_empresa: str, documentos) -> tuple[int, int]:
        notas, eventos = 0, 0
        for doc in documentos:
            tipo = doc.tipo_documento or ''
            if tipo not in ('NFSE', 'EVENTO'):
                try:
                    tipo = fiscal_nfse.tipo_documento(doc.xml_bytes)
                except Exception:
                    continue
            if tipo == 'NFSE':
                if self._gravar_nota_nfse(doc_empresa, doc):
                    notas += 1
            elif tipo == 'EVENTO':
                if self._gravar_evento_nfse(doc):
                    eventos += 1
        db.session.commit()
        return notas, eventos

    def _gravar_nota_nfse(self, doc_empresa: str, doc) -> bool:
        try:
            extraida = fiscal_nfse.parse_nfse(doc.xml_bytes)
        except Exception:
            return False
        chave = extraida.chave_acesso or doc.chave_acesso
        if not chave:
            return False

        registro = NotaServico.query.filter_by(chave_acesso=chave).first()
        nova = registro is None
        if nova:
            registro = NotaServico(chave_acesso=chave, situacao='ATIVA')
            db.session.add(registro)

        registro.nsu = doc.nsu
        registro.papel = classificar_papel_nfse(doc_empresa, extraida)
        registro.numero = extraida.numero
        registro.serie = extraida.serie
        registro.data_emissao = _parse_data(extraida.data_emissao)
        registro.competencia = extraida.competencia
        registro.prestador_doc = extraida.prestador_doc
        registro.prestador_nome = extraida.prestador_nome
        registro.tomador_doc = extraida.tomador_doc
        registro.tomador_nome = extraida.tomador_nome
        registro.municipio = extraida.municipio
        registro.descricao_servico = extraida.descricao_servico
        registro.valor_servico = extraida.valor_servico
        registro.valor_liquido = extraida.valor_liquido
        registro.valor_iss = extraida.valor_iss
        registro.xml_gzip = gzip.compress(doc.xml_bytes)
        return nova

    @staticmethod
    def _gravar_evento_nfse(doc) -> bool:
        try:
            evento = fiscal_nfse.parse_evento(doc.xml_bytes)
        except Exception:
            return False
        chave = evento.chave_acesso or doc.chave_acesso
        if not chave:
            return False
        existe = NotaServicoEvento.query.filter_by(
            chave_acesso=chave, tipo_evento=evento.tipo_evento, nsu=doc.nsu
        ).first()
        if existe:
            return False
        db.session.add(NotaServicoEvento(
            chave_acesso=chave,
            nsu=doc.nsu,
            tipo_evento=evento.tipo_evento,
            descricao=evento.descricao[:300],
            dh_evento=evento.dh_evento,
        ))
        return True

    @staticmethod
    def _aplicar_eventos_nfse() -> None:
        """Recalcula a situação das notas com base nos eventos recebidos."""
        situacoes: dict[str, str] = {}
        for evento in NotaServicoEvento.query.all():
            situacao = fiscal_nfse.evento_cancela(evento.tipo_evento, evento.descricao)
            if situacao:
                situacoes[evento.chave_acesso] = situacao
        for chave, situacao in situacoes.items():
            NotaServico.query.filter_by(chave_acesso=chave).update({'situacao': situacao})
        db.session.commit()

    # -------------------------- NF-e -------------------------------- #

    def nfe_minutos_de_espera(self) -> int:
        """Minutos até poder consultar a SEFAZ de novo (0 = liberado).

        A regra vale depois de um 137 (nada novo) ou 656 (consumo indevido).
        """
        cstat = config_get('nfe_ultimo_cstat')
        quando = config_get('nfe_ultima_consulta_em')
        if cstat not in ('137', '656') or not quando:
            return 0
        try:
            anterior = dt.datetime.fromisoformat(quando)
        except ValueError:
            return 0
        falta = dt.timedelta(minutes=NFE_ESPERA_MINUTOS) - (dt.datetime.now() - anterior)
        segundos = falta.total_seconds()
        return int(segundos // 60) + 1 if segundos > 0 else 0

    def _executar_nfe(self) -> None:
        with self.app.app_context():
            try:
                caminho, senha = self._credenciais()
                doc_empresa = _digitos(config_get('cert_documento'))
                ambiente = config_get('nfe_ambiente', 'producao')
                uf_autor = config_get('nfe_uf_autor', '35')
                ult_nsu = config_get('nfe_ultimo_nsu', '000000000000000')

                novos = 0
                eventos = 0
                cstat = ''
                xmotivo = ''

                with fiscal_certificado.CertificadoContext(caminho, senha) as ctx:
                    if ctx.documento and doc_empresa and ctx.documento != doc_empresa:
                        raise fiscal_certificado.CertificadoInvalido(
                            f'O certificado pertence ao CNPJ {ctx.documento}, '
                            f'diferente do configurado ({doc_empresa}).'
                        )
                    for _ in range(NFE_MAX_LOTES):
                        self._atualizar('nfe', mensagem=f'Consultando a SEFAZ a partir do NSU {int(ult_nsu)}...')
                        resultado = fiscal_nfe.consultar_distnsu(
                            cnpj=doc_empresa,
                            uf_autor=uf_autor,
                            ambiente=ambiente,
                            ult_nsu=ult_nsu,
                            cert_pair=ctx.cert_pair,
                            url=os.environ.get('ERP_NFE_URL'),
                            verify=self._verify(),
                        )
                        cstat = resultado['cstat']
                        xmotivo = resultado['xmotivo']

                        n_novos, n_eventos = self._gravar_lote_nfe(doc_empresa, resultado['documentos'])
                        novos += n_novos
                        eventos += n_eventos

                        ult_nsu = resultado['ult_nsu']
                        config_set('nfe_ultimo_nsu', ult_nsu)
                        self._atualizar('nfe', novos=novos)

                        tem_mais = cstat == '138' and resultado['ult_nsu'] != resultado['max_nsu']
                        if not tem_mais:
                            break
                        time.sleep(PAUSA_ENTRE_LOTES)

                config_set('nfe_ultimo_cstat', cstat)
                config_set('nfe_ultima_consulta_em', dt.datetime.now().isoformat())
                config_set('nfe_ultima_sync', _agora())

                mensagem = (
                    f'Sincronização concluída: {novos} nota(s) nova(s)'
                    + (f' e {eventos} evento(s)' if eventos else '')
                    + f'. SEFAZ: [{cstat}] {xmotivo}'
                )
                config_set('nfe_ultimo_status', mensagem)
                self._atualizar('nfe', estado='concluido', mensagem=mensagem, novos=novos)
            except fiscal_nfe.ErroSefaz as exc:
                config_set('nfe_ultimo_cstat', exc.cstat)
                config_set('nfe_ultima_consulta_em', dt.datetime.now().isoformat())
                if exc.cstat == '656':
                    mensagem = (
                        'A SEFAZ bloqueou temporariamente novas consultas deste CNPJ '
                        '(consumo indevido — consultas repetidas em menos de 1 hora). '
                        'O bloqueio expira sozinho em cerca de 1 hora.'
                    )
                else:
                    mensagem = f'SEFAZ rejeitou: [{exc.cstat}] {exc.xmotivo}'
                config_set('nfe_ultimo_status', mensagem)
                self._atualizar('nfe', estado='erro', mensagem=mensagem)
            except fiscal_certificado.CertificadoInvalido as exc:
                config_set('nfe_ultimo_status', f'Erro: {exc}')
                self._atualizar('nfe', estado='erro', mensagem=str(exc))
            except Exception as exc:
                config_set('nfe_ultimo_status', f'Erro inesperado: {exc}')
                self._atualizar('nfe', estado='erro', mensagem=f'Erro inesperado na sincronização: {exc}')

    def _gravar_lote_nfe(self, doc_empresa: str, documentos) -> tuple[int, int]:
        novos, eventos = 0, 0
        for doc in documentos:
            if doc['tipo'] == 'resNFe':
                dados = fiscal_nfe.extrair_dados_resumo(doc['xml'])
            elif doc['tipo'] == 'nfeProc':
                dados = fiscal_nfe.extrair_dados_nfeproc(doc['xml'])
            elif doc['tipo'] == 'resEvento':
                eventos += 1
                continue
            else:
                continue
            if self._gravar_nota_nfe(doc_empresa, doc, dados):
                novos += 1
        db.session.commit()
        return novos, eventos

    @staticmethod
    def _gravar_nota_nfe(doc_empresa: str, doc, dados) -> bool:
        chave = dados.get('chave_nfe')
        if not chave:
            return False

        registro = NotaEletronica.query.filter_by(chave=chave).first()
        nova = registro is None
        if nova:
            registro = NotaEletronica(chave=chave)
            db.session.add(registro)
        elif registro.tipo_doc == 'nfeProc' and doc['tipo'] == 'resNFe':
            # A completa já está salva: o resumo não acrescenta nada.
            return False

        registro.nsu = doc['nsu']
        registro.tipo_doc = doc['tipo']
        registro.papel = classificar_papel_nfe(doc_empresa, dados.get('emitente_cnpj'))
        registro.emitente_doc = _digitos(dados.get('emitente_cnpj'))
        registro.emitente_nome = dados.get('emitente_nome')
        if dados.get('dest_cnpj'):
            registro.dest_doc = _digitos(dados.get('dest_cnpj'))
        if dados.get('dest_nome'):
            registro.dest_nome = dados.get('dest_nome')
        registro.valor_total = dados.get('valor_total')
        registro.data_emissao = _parse_data(dados.get('data_emissao'))
        registro.situacao = dados.get('situacao')
        registro.xml_gzip = gzip.compress(doc['xml'].encode('utf-8'))
        registro.baixado_em = _agora()
        return nova
