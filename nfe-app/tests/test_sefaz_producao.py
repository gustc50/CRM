"""O que sai pela rede quando o ambiente é PRODUÇÃO.

Intercepta o requests.post e inspeciona o envelope SOAP, para que um erro de
endpoint ou de tpAmb apareça aqui e não numa consulta real da SEFAZ.
"""
import base64
import gzip
import sys
import types
import unittest
from xml.etree import ElementTree as ET

import _ambiente

_, _, sefaz_client = _ambiente.preparar()

NS = "http://www.portalfiscal.inf.br/nfe"
URL_PRODUCAO = "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"

RESUMO_NFE = (
    f'<resNFe xmlns="{NS}" versao="1.01">'
    "<chNFe>35260912345678000199550010000001231000001234</chNFe>"
    "<CNPJ>12345678000199</CNPJ><xNome>Fornecedor Exemplo LTDA</xNome>"
    "<dhEmi>2026-09-14T09:15:00-03:00</dhEmi><tpNF>1</tpNF><vNF>1543.20</vNF>"
    "<nProt>135260000012345</nProt><cSitNFe>1</cSitNFe></resNFe>"
)


def montar_resposta(cstat, xmotivo, ult_nsu="000000000000501",
                    max_nsu="000000000000501", docs=(), tp_amb="1"):
    """Resposta SOAP no mesmo formato da SEFAZ (docZip = gzip + base64)."""
    lote = ""
    for nsu, schema, xml in docs:
        b64 = base64.b64encode(gzip.compress(xml.encode("utf-8"))).decode()
        lote += f'<docZip NSU="{nsu}" schema="{schema}">{b64}</docZip>'
    if lote:
        lote = f"<loteDistDFeInt>{lote}</loteDistDFeInt>"
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
        f'<nfeDistDFeInteresseResponse xmlns="{NS}/wsdl/NFeDistribuicaoDFe">'
        f'<nfeDistDFeInteresseResult><retDistDFeInt xmlns="{NS}" versao="1.01">'
        f"<tpAmb>{tp_amb}</tpAmb><verAplic>1.0</verAplic><cStat>{cstat}</cStat>"
        f"<xMotivo>{xmotivo}</xMotivo><dhResp>2026-09-15T18:00:00-03:00</dhResp>"
        f"<ultNSU>{ult_nsu}</ultNSU><maxNSU>{max_nsu}</maxNSU>{lote}"
        "</retDistDFeInt></nfeDistDFeInteresseResult>"
        "</nfeDistDFeInteresseResponse></soap:Body></soap:Envelope>"
    )


class _Resposta:
    def __init__(self, texto, status=200):
        self.text = texto
        self.status_code = status


class BaseSefaz(unittest.TestCase):
    def setUp(self):
        self.enviado = {}
        self.fila = []
        self._post_real = sefaz_client.requests

        def post(url, data=None, headers=None, cert=None, timeout=None):
            self.enviado = dict(url=url, corpo=data.decode("utf-8"),
                                headers=headers, cert=cert, timeout=timeout)
            if self.fila:
                return self.fila.pop(0)
            return _Resposta(montar_resposta(
                "138", "Documento(s) localizado(s)",
                docs=[(501, "resNFe_v1.01.xsd", RESUMO_NFE)]))

        sefaz_client.requests = types.SimpleNamespace(post=post)

    def tearDown(self):
        sefaz_client.requests = self._post_real

    def consultar(self, ambiente="producao", ult_nsu="000000000000500",
                  cert_pair=("/fake/cert.pem", "/fake/key.pem")):
        return sefaz_client.consultar_distnsu(
            cnpj=_ambiente.CNPJ_EMPRESA, uf_autor="35", ambiente=ambiente,
            ult_nsu=ult_nsu, cert_pair=cert_pair)

    def dist_enviado(self):
        return ET.fromstring(self.enviado["corpo"]).find(f".//{{{NS}}}distDFeInt")


class TestEnvelopeProducao(BaseSefaz):
    def test_usa_endpoint_de_producao(self):
        self.consultar()
        self.assertEqual(self.enviado["url"], URL_PRODUCAO)
        self.assertNotIn("hom", self.enviado["url"].split("//")[1].split(".")[0])

    def test_tp_amb_1_em_producao(self):
        self.consultar()
        self.assertEqual(self.dist_enviado().find(f"{{{NS}}}tpAmb").text, "1")

    def test_tp_amb_acompanha_a_url(self):
        """tpAmb e endpoint nunca podem divergir: produção com tpAmb=2 é rejeitado."""
        for ambiente, marca_url, tp_amb in [("producao", "www1", "1"),
                                            ("homologacao", "hom1", "2")]:
            with self.subTest(ambiente=ambiente):
                self.fila.append(_Resposta(montar_resposta(
                    "137", "Nenhum documento localizado", tp_amb=tp_amb)))
                self.consultar(ambiente=ambiente)
                self.assertIn(marca_url, self.enviado["url"])
                self.assertEqual(self.dist_enviado().find(f"{{{NS}}}tpAmb").text, tp_amb)

    def test_envia_cnpj_uf_e_cursor(self):
        self.consultar(ult_nsu="000000000000500")
        dist = self.dist_enviado()
        self.assertEqual(dist.find(f"{{{NS}}}CNPJ").text, _ambiente.CNPJ_EMPRESA)
        self.assertEqual(dist.find(f"{{{NS}}}cUFAutor").text, "35")
        self.assertEqual(dist.find(f".//{{{NS}}}ultNSU").text, "000000000000500")
        self.assertEqual(dist.get("versao"), "1.01")

    def test_uma_unica_declaracao_xml(self):
        """Uma <?xml?> no meio do envelope deixa a mensagem malformada (HTTP 400)."""
        self.consultar()
        self.assertEqual(self.enviado["corpo"].count("<?xml"), 1)

    def test_autentica_com_o_certificado_do_cliente(self):
        self.consultar()
        self.assertEqual(self.enviado["cert"], ("/fake/cert.pem", "/fake/key.pem"))
        self.assertTrue(self.enviado["url"].startswith("https://"))
        self.assertEqual(self.enviado["timeout"], 30)

    def test_cabecalho_soap_12_com_action(self):
        self.consultar()
        tipo = self.enviado["headers"]["Content-Type"]
        self.assertIn("application/soap+xml", tipo)
        self.assertIn("nfeDistDFeInteresse", tipo)


class TestLeituraDaResposta(BaseSefaz):
    def test_le_lote_com_resumo_compactado(self):
        r = self.consultar()
        self.assertEqual(r["cstat"], "138")
        self.assertEqual(len(r["documentos"]), 1)
        doc = r["documentos"][0]
        self.assertEqual(doc["tipo"], "resNFe")
        self.assertEqual(doc["nsu"], "501")
        self.assertIn("<resNFe", doc["xml"])

    def test_extrai_campos_do_resumo(self):
        doc = self.consultar()["documentos"][0]
        d = sefaz_client.extrair_dados_resumo(doc["xml"])
        self.assertEqual(len(d["chave_nfe"]), 44)
        self.assertEqual(d["emitente_nome"], "Fornecedor Exemplo LTDA")
        self.assertEqual(d["emitente_cnpj"], "12345678000199")
        self.assertEqual(d["valor_total"], 1543.20)
        self.assertEqual(d["situacao"], "1")
        self.assertTrue(d["data_emissao"].startswith("2026-09-14"))

    def test_137_nao_e_erro(self):
        self.fila.append(_Resposta(montar_resposta("137", "Nenhum documento localizado")))
        self.assertEqual(self.consultar()["cstat"], "137")

    def test_cstat_de_erro_preserva_o_codigo(self):
        for cstat, motivo in [("656", "Consumo Indevido"),
                              ("215", "Falha no schema XML"),
                              ("286", "Certificado nao corresponde ao CNPJ")]:
            with self.subTest(cstat=cstat):
                self.fila.append(_Resposta(montar_resposta(cstat, motivo)))
                with self.assertRaises(sefaz_client.ErroSefaz) as ctx:
                    self.consultar()
                self.assertEqual(ctx.exception.cstat, cstat)

    def test_erro_http_vira_mensagem_legivel(self):
        self.fila.append(_Resposta("<html><body>Service Unavailable</body></html>", status=503))
        with self.assertRaises(sefaz_client.ErroSefaz) as ctx:
            self.consultar()
        self.assertEqual(ctx.exception.cstat, "503")
        self.assertIn("503", ctx.exception.xmotivo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
