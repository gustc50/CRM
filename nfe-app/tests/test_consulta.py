"""Regras da consulta: janela de 1h da SEFAZ, cursor NSU e gravação das notas.

Boa parte destes testes fixa bugs que já morderam em produção e cujo sintoma
era sempre o mesmo — o CNPJ entrar em 656 (consumo indevido) e o app ficar
inutilizável por uma hora. Os nomes marcam o que cada um protege.
"""
import unittest

import _ambiente

db, main, sefaz_client = _ambiente.preparar()

NS = "http://www.portalfiscal.inf.br/nfe"
CHAVE = "35260912345678000199550010000001231000001234"


def resumo(chave=CHAVE, nome="Fornecedor A", valor="1543.20",
           data="2026-09-14T09:15:00-03:00"):
    return (f'<resNFe xmlns="{NS}" versao="1.01"><chNFe>{chave}</chNFe>'
            f"<CNPJ>{chave[6:20]}</CNPJ><xNome>{nome}</xNome><dhEmi>{data}</dhEmi>"
            f"<tpNF>1</tpNF><vNF>{valor}</vNF><cSitNFe>1</cSitNFe></resNFe>")


def nfe_completa(chave=CHAVE):
    return (f'<nfeProc xmlns="{NS}" versao="4.00"><NFe><infNFe Id="NFe{chave}">'
            "<ide><dhEmi>2026-09-14T09:15:00-03:00</dhEmi></ide>"
            "<emit><CNPJ>12345678000199</CNPJ><xNome>Fornecedor A</xNome></emit>"
            "<total><ICMSTot><vNF>1543.20</vNF></ICMSTot></total></infNFe></NFe>"
            f"<protNFe><infProt><chNFe>{chave}</chNFe></infProt></protNFe></nfeProc>")


def lote(cstat="138", ult_nsu="000000000000002", documentos=(), xmotivo=None):
    return {
        "cstat": cstat,
        "xmotivo": xmotivo or ("Documento(s) localizado(s)" if cstat == "138"
                               else "Nenhum documento localizado"),
        "ult_nsu": ult_nsu,
        "max_nsu": ult_nsu,
        "documentos": list(documentos),
    }


def doc(tipo, xml, nsu="1"):
    return {"nsu": nsu, "tipo": tipo, "schema": f"{tipo}_v1.01.xsd", "xml": xml}


class BaseConsulta(unittest.TestCase):
    def setUp(self):
        _ambiente.banco_limpo(db)
        self.api = main.Api()
        self.empresa_id = db.criar_empresa(
            "Cliente Produção LTDA", _ambiente.CNPJ_EMPRESA, "/fake/cert.pfx",
            "producao", "35")
        self.fila = []
        self._real = sefaz_client.consultar_distnsu

        def consultar(cnpj, uf_autor, ambiente, ult_nsu, cert_pair):
            self.assertEqual(ambiente, "producao")
            item = self.fila.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        sefaz_client.consultar_distnsu = consultar

    def tearDown(self):
        sefaz_client.consultar_distnsu = self._real

    def consultar(self, senha=_ambiente.SENHA_CERTA, **kw):
        return self.api.consultar_notas(self.empresa_id, senha_certificado=senha, **kw)

    def cursor(self):
        return db.obter_empresa(self.empresa_id)["ultimo_nsu"]


class TestCursorNSU(BaseConsulta):
    """O cursor só anda pra frente.

    A SEFAZ às vezes responde 137 com ultNSU zerado. Gravar isso jogava o
    cursor pro início e a consulta seguinte re-baixava ~90 dias em vários
    lotes, queimando a cota de consumo até cair em 656.
    """

    def test_cursor_avanca_com_documentos(self):
        self.fila.append(lote(documentos=[doc("resNFe", resumo())]))
        self.consultar()
        self.assertEqual(self.cursor(), "000000000000002")

    def test_cursor_nao_retrocede_quando_sefaz_devolve_ultnsu_zerado(self):
        db.atualizar_ultimo_nsu(self.empresa_id, "000000000000500")
        self.fila.append(lote(cstat="137", ult_nsu="000000000000000"))
        self.consultar(forcar=True)
        self.assertEqual(self.cursor(), "000000000000500")

    def test_cursor_nao_retrocede_em_gravacao_direta(self):
        db.atualizar_ultimo_nsu(self.empresa_id, "000000000000500")
        db.atualizar_ultimo_nsu(self.empresa_id, "000000000000450")
        self.assertEqual(self.cursor(), "000000000000500")

    def test_resincronizar_ainda_consegue_zerar(self):
        """A trava acima não pode matar o botão "Re-sincronizar do zero"."""
        db.atualizar_ultimo_nsu(self.empresa_id, "000000000000500")
        self.api.resincronizar(self.empresa_id)
        self.assertEqual(self.cursor(), "000000000000000")
        db.atualizar_ultimo_nsu(self.empresa_id, "000000000000050")
        self.assertEqual(self.cursor(), "000000000000050")


class TestJanelaDeUmaHora(BaseConsulta):
    def test_137_abre_a_janela_de_espera(self):
        self.fila.append(lote(cstat="137"))
        self.consultar()
        self.assertGreater(self.api.estado_consulta(self.empresa_id)["minutos_espera"], 0)

    def test_consulta_bloqueada_nao_gasta_chamada(self):
        self.fila.append(lote(cstat="137"))
        self.consultar()
        r = self.consultar()
        self.assertTrue(r.get("aguardar"))
        self.assertEqual(self.fila, [])  # nada foi consumido da fila

    def test_contador_e_por_cnpj(self):
        outra = db.criar_empresa("Outro Cliente", "99888777000166", "/o.pfx",
                                 "producao", "35")
        self.fila.append(lote(cstat="137"))
        self.consultar()
        self.assertGreater(self.api.estado_consulta(self.empresa_id)["minutos_espera"], 0)
        self.assertEqual(self.api.estado_consulta(outra)["minutos_espera"], 0)

    def test_656_e_reportado_como_espera_e_nao_como_falha(self):
        self.fila.append(sefaz_client.ErroSefaz("656", "Consumo Indevido"))
        r = self.consultar()
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("aguardar"))


class TestResincronizar(BaseConsulta):
    """Re-sincronizar não pode disparar consulta forçada.

    Quem clica no botão acabou de receber um 137, então a janela de 1h está
    sempre correndo. A consulta forçada voltava 656, e o próprio 656
    reiniciava o bloqueio: cada tentativa renovava a espera e a re-busca
    nunca rodava.
    """

    def test_nao_dispara_consulta_quando_a_janela_esta_correndo(self):
        db.atualizar_ultimo_nsu(self.empresa_id, "000000000000500")
        db.registrar_log(self.empresa_id, "137", "Nenhum documento localizado", 0)
        espera_antes = self.api._minutos_de_espera(self.empresa_id)

        r = self.api.resincronizar(self.empresa_id)

        self.assertGreater(r["minutos_espera"], 0)          # a UI precisa saber
        self.assertEqual(self.cursor(), "000000000000000")  # re-busca fica armada
        self.assertEqual(self.fila, [])                     # nenhuma chamada saiu
        self.assertEqual(db.ultima_consulta(self.empresa_id)["cstat"], "137")
        self.assertLessEqual(self.api._minutos_de_espera(self.empresa_id), espera_antes)

    def test_informa_janela_livre_quando_nao_ha_espera(self):
        self.assertEqual(self.api.resincronizar(self.empresa_id)["minutos_espera"], 0)


class TestSenhaECertificado(BaseConsulta):
    def test_sem_senha_pede_a_senha(self):
        r = self.api.consultar_notas(self.empresa_id)
        self.assertTrue(r.get("senha_necessaria"))

    def test_senha_correta_fica_em_cache(self):
        self.fila.append(lote(cstat="137"))
        self.consultar()
        self.assertTrue(self.api.estado_consulta(self.empresa_id)["senha_em_cache"])

    def test_senha_errada_nao_fica_em_cache(self):
        r = self.consultar(senha="errada")
        self.assertTrue(r.get("senha_necessaria"))
        self.assertFalse(self.api.estado_consulta(self.empresa_id)["senha_em_cache"])

    def test_certificado_de_outro_cnpj_e_barrado_antes_da_sefaz(self):
        outra = db.criar_empresa("Outro Cliente", "99888777000166", "/o.pfx",
                                 "producao", "35")
        r = self.api.consultar_notas(outra, senha_certificado=_ambiente.SENHA_CERTA)
        self.assertFalse(r["ok"])
        self.assertIn("99888777000166", r["erro"])
        self.assertEqual(self.fila, [])


class TestGravacaoDasNotas(BaseConsulta):
    def test_resumo_vira_linha_na_lista(self):
        self.fila.append(lote(documentos=[doc("resNFe", resumo())]))
        r = self.consultar()
        self.assertEqual(r["novos_documentos"], 1)
        notas = db.listar_notas(self.empresa_id)
        self.assertEqual(len(notas), 1)
        self.assertEqual(notas[0]["emitente_nome"], "Fornecedor A")
        self.assertEqual(notas[0]["valor_total"], 1543.20)

    def test_evento_nao_vira_nota(self):
        self.fila.append(lote(documentos=[
            doc("resEvento", f'<resEvento xmlns="{NS}"><chNFe>x</chNFe></resEvento>')]))
        r = self.consultar()
        self.assertEqual(r["novos_documentos"], 0)
        self.assertEqual(r["eventos"], 1)
        self.assertEqual(db.listar_notas(self.empresa_id), [])

    def test_nota_completa_substitui_o_resumo_sem_duplicar(self):
        self.fila.append(lote(documentos=[doc("resNFe", resumo())]))
        self.consultar()
        self.fila.append(lote(ult_nsu="000000000000003",
                              documentos=[doc("nfeProc", nfe_completa(), nsu="3")]))
        self.consultar(forcar=True)
        notas = db.listar_notas(self.empresa_id)
        self.assertEqual(len(notas), 1)
        self.assertEqual(notas[0]["tipo_doc"], "nfeProc")

    def test_avisa_quando_a_sefaz_pula_numeros_de_sequencia(self):
        db.atualizar_ultimo_nsu(self.empresa_id, "000000000000100")
        self.fila.append(lote(cstat="137", ult_nsu="000000000000150"))
        r = self.consultar(forcar=True)
        self.assertEqual(r["nsu_pulados"], 50)


class TestFiltroDeDatas(BaseConsulta):
    """O aviso da interface conta quantas notas o período esconde."""

    def _semear(self):
        for i, data in enumerate(["2026-09-10T10:00:00", "2026-09-12T10:00:00",
                                  "2019-01-10T10:00:00", "2018-05-12T10:00:00"]):
            db.salvar_nota(self.empresa_id, f"{i}" * 44, str(i), "resNFe",
                           "12345678000199", "Fornecedor", 10.0, data, "1", "<xml/>")

    def test_conta_notas_escondidas_pelo_periodo(self):
        self._semear()
        total = len(db.listar_notas(self.empresa_id))
        no_periodo = len(db.listar_notas(self.empresa_id, "2026-01-01", "2026-12-31"))
        self.assertEqual(total, 4)
        self.assertEqual(total - no_periodo, 2)

    def test_periodo_amplo_nao_esconde_nada(self):
        self._semear()
        total = len(db.listar_notas(self.empresa_id))
        self.assertEqual(len(db.listar_notas(self.empresa_id, "2000-01-01", "2030-01-01")),
                         total)

    def test_nota_sem_data_nunca_some(self):
        db.salvar_nota(self.empresa_id, "9" * 44, "9", "nfeProc", None, None,
                       None, None, "1", "<xml/>")
        self.assertEqual(len(db.listar_notas(self.empresa_id, "2026-01-01", "2026-12-31")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
