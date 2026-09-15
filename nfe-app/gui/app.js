let empresaAtual = null;
let certValidado = null; // guarda { path } depois de validar com sucesso
let filtroTipo = 'todas'; // 'todas' | 'recebidas' | 'emitidas'

// Códigos IBGE de UF — os 2 primeiros dígitos da chave de acesso dizem
// em qual estado a nota foi emitida.
const UF_POR_CODIGO = {
  '11': 'RO', '12': 'AC', '13': 'AM', '14': 'RR', '15': 'PA', '16': 'AP', '17': 'TO',
  '21': 'MA', '22': 'PI', '23': 'CE', '24': 'RN', '25': 'PB', '26': 'PE', '27': 'AL',
  '28': 'SE', '29': 'BA', '31': 'MG', '32': 'ES', '33': 'RJ', '35': 'SP',
  '41': 'PR', '42': 'SC', '43': 'RS', '50': 'MS', '51': 'MT', '52': 'GO', '53': 'DF',
};

const CODIGO_POR_UF = {};
Object.keys(UF_POR_CODIGO).forEach(cod => { CODIGO_POR_UF[UF_POR_CODIGO[cod]] = cod; });

function ufDaChave(chave) {
  if (!chave || chave.length < 2) return '—';
  return UF_POR_CODIGO[chave.slice(0, 2)] || '—';
}

// Na chave de acesso, as posições 7–20 são o CNPJ do emitente — serve de
// fallback pra notas antigas salvas sem o campo emitente preenchido.
function cnpjEmitenteDaChave(chave) {
  return chave && chave.length === 44 ? chave.slice(6, 20) : null;
}

function api() {
  return window.pywebview.api;
}

function mostrarTela(id) {
  document.querySelectorAll('.tela, #tela-vazia').forEach(el => el.classList.add('hidden'));
  document.getElementById(id).classList.remove('hidden');
}

// ---------------- Lista de empresas ----------------

async function carregarEmpresas(selecionarId) {
  const empresas = await api().listar_empresas();
  const nav = document.getElementById('lista-empresas');
  nav.innerHTML = '';

  empresas.forEach(emp => {
    const div = document.createElement('div');
    div.className = 'item-empresa' + (emp.id === selecionarId ? ' ativo' : '');
    div.innerHTML = `
      <span class="nome">${escapeHtml(emp.razao_social)}</span>
      <span class="cnpj">${formatarCnpj(emp.cnpj)}</span>
    `;
    div.onclick = () => abrirEmpresa(emp.id);
    nav.appendChild(div);
  });

  if (selecionarId) {
    const emp = empresas.find(e => e.id === selecionarId);
    if (emp) preencherTelaEmpresa(emp);
  }
}

async function abrirEmpresa(id) {
  const empresas = await api().listar_empresas();
  const emp = empresas.find(e => e.id === id);
  if (!emp) return;
  document.querySelectorAll('.item-empresa').forEach(el => el.classList.remove('ativo'));
  await carregarEmpresas(id);
  preencherTelaEmpresa(emp);
}

// Data local, não UTC: toISOString() converte pro fuso zero e desloca o dia,
// o que fazia o período padrão cortar as notas emitidas hoje.
function dataIsoSemHora(date) {
  const mes = String(date.getMonth() + 1).padStart(2, '0');
  const dia = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${mes}-${dia}`;
}

async function preencherTelaEmpresa(emp) {
  empresaAtual = emp;
  document.getElementById('e-razao').textContent = emp.razao_social;
  document.getElementById('e-cnpj').textContent = formatarCnpj(emp.cnpj) + ' · ' + emp.ambiente;
  document.getElementById('status-consulta').textContent = '';
  document.getElementById('status-consulta').className = 'status-consulta';
  document.getElementById('log-consulta').classList.add('hidden');

  aplicarFiltroDatasPadrao();

  mostrarTela('tela-empresa');
  await atualizarEstadoConsulta();
  await recarregarNotas(emp.id);
}

function aplicarFiltroDatasPadrao() {
  const hoje = new Date();
  const cincoAnosAtras = new Date(hoje);
  cincoAnosAtras.setFullYear(cincoAnosAtras.getFullYear() - 5);
  document.getElementById('f-data-inicio').value = dataIsoSemHora(cincoAnosAtras);
  document.getElementById('f-data-fim').value = dataIsoSemHora(hoje);
}

function limparFiltroDatas() {
  document.getElementById('f-data-inicio').value = '';
  document.getElementById('f-data-fim').value = '';
}

// Aviso permanente enquanto o período esconder qualquer nota já baixada —
// não só quando a lista fica vazia.
function atualizarAvisoFiltroData(empresaId, visiveisNoPeriodo, totalBaixadas) {
  const el = document.getElementById('aviso-filtro-data');
  const escondidas = Math.max(0, totalBaixadas - visiveisNoPeriodo);

  if (!escondidas) {
    el.classList.add('hidden');
    el.innerHTML = '';
    return;
  }

  el.classList.remove('hidden');
  el.innerHTML =
    `⚠️ O filtro de datas está escondendo <strong>${escondidas}</strong> de ` +
    `<strong>${totalBaixadas}</strong> nota(s) já baixada(s) deste CNPJ. ` +
    `Elas continuam salvas no app — só estão fora do período escolhido acima. ` +
    `<a href="#" id="link-limpar-filtro">Mostrar todas</a>`;

  document.getElementById('link-limpar-filtro').onclick = (ev) => {
    ev.preventDefault();
    limparFiltroDatas();
    recarregarNotas(empresaId);
  };
}

// Mostra, POR EMPRESA, se a busca está liberada ou quanto falta pra janela
// de 1h da SEFAZ abrir de novo. Cada CNPJ tem contador próprio e independente.
// Quando o contador zera e a senha já está na sessão, re-consulta sozinho
// (rotina de 1x/hora recomendada pela própria SEFAZ).
let timerEspera = null;
let empresaAguardando = null; // id da empresa cujo contador estamos vigiando

async function atualizarEstadoConsulta() {
  if (!empresaAtual) return;
  const btn = document.getElementById('btn-consultar');
  const infoEl = document.getElementById('info-espera');

  const r = await api().estado_consulta(empresaAtual.id);

  if (r.minutos_espera > 0) {
    empresaAguardando = empresaAtual.id;
    btn.disabled = true;
    infoEl.classList.remove('hidden');
    let quando = '';
    if (r.ultima_hora) {
      const d = new Date(r.ultima_hora);
      if (!isNaN(d)) quando = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
    }
    infoEl.innerHTML =
      `⏳ Próxima busca deste CNPJ liberada em ~${r.minutos_espera} min` +
      (quando ? ` — última resposta da SEFAZ: código ${r.ultimo_cstat} às ${quando}` : '') + `. ` +
      (r.senha_em_cache
        ? `O app vai refazer a busca AUTOMATICAMENTE quando liberar — basta deixá-lo aberto. `
        : `Quando liberar, clique em "Buscar novas notas". `) +
      `Vale só pra ESTE CNPJ (os outros clientes têm contador próprio) e os filtros de data ` +
      `abaixo não influenciam — eles só filtram a lista já baixada. ` +
      `<a href="#" id="link-forcar">Consultar assim mesmo</a> — por sua conta: se a SEFAZ ` +
      `ainda considerar cedo, ela responde 656 e a espera recomeça.`;
    document.getElementById('link-forcar').onclick = (ev) => {
      ev.preventDefault();
      executarConsulta(true);
    };
    if (!timerEspera) timerEspera = setInterval(atualizarEstadoConsulta, 30000);
  } else {
    btn.disabled = false;
    infoEl.classList.add('hidden');
    infoEl.textContent = '';
    if (timerEspera) { clearInterval(timerEspera); timerEspera = null; }

    // Contador desta empresa acabou de zerar com a senha em cache? Re-consulta sozinho.
    if (empresaAguardando === empresaAtual.id && r.senha_em_cache) {
      empresaAguardando = null;
      executarConsulta(false);
    } else {
      empresaAguardando = null;
    }
  }
}

document.getElementById('f-data-inicio').onchange = () => empresaAtual && recarregarNotas(empresaAtual.id);
document.getElementById('f-data-fim').onchange = () => empresaAtual && recarregarNotas(empresaAtual.id);

document.querySelectorAll('#filtro-tipo button').forEach(btn => {
  btn.onclick = () => {
    filtroTipo = btn.dataset.tipo;
    document.querySelectorAll('#filtro-tipo button').forEach(b => b.classList.toggle('ativo', b === btn));
    if (empresaAtual) recarregarNotas(empresaAtual.id);
  };
});

function notaEhEmitida(n) {
  const emitCnpj = n.emitente_cnpj || cnpjEmitenteDaChave(n.chave_nfe);
  return !!(emitCnpj && empresaAtual && emitCnpj === empresaAtual.cnpj);
}

async function recarregarNotas(empresaId) {
  const dataInicio = document.getElementById('f-data-inicio').value || null;
  const dataFim = document.getElementById('f-data-fim').value || null;
  const notas = await api().listar_notas(empresaId, dataInicio, dataFim);
  const corpo = document.getElementById('corpo-tabela-notas');

  // O filtro de datas esconde notas em silêncio. Antes o aviso só aparecia
  // quando a lista ficava COMPLETAMENTE vazia — então quem tinha 40 notas e
  // via 3 achava que só existiam 3. Agora o aviso acompanha qualquer nota
  // escondida pelo período. A consulta extra é no SQLite local, é barata.
  const semFiltroData = (dataInicio || dataFim)
    ? await api().listar_notas(empresaId, null, null)
    : notas;
  atualizarAvisoFiltroData(empresaId, notas.length, semFiltroData.length);

  const emitidas = notas.filter(notaEhEmitida);
  const recebidas = notas.filter(n => !notaEhEmitida(n));

  document.querySelector('#filtro-tipo [data-tipo="todas"]').textContent = `Todas (${notas.length})`;
  document.querySelector('#filtro-tipo [data-tipo="recebidas"]').textContent = `Recebidas (${recebidas.length})`;
  document.querySelector('#filtro-tipo [data-tipo="emitidas"]').textContent = `Emitidas (${emitidas.length})`;

  const visiveis = filtroTipo === 'emitidas' ? emitidas
                 : filtroTipo === 'recebidas' ? recebidas
                 : notas;

  if (!visiveis.length) {
    // O aviso acima já explica o que o período escondeu; aqui só dizemos por
    // que ESTA lista está vazia.
    const msg = !semFiltroData.length
      ? 'Nenhuma nota baixada ainda — clique em "Buscar novas notas".'
      : (notas.length && filtroTipo !== 'todas')
        ? `Nenhuma nota ${filtroTipo} no período selecionado.`
        : 'Nenhuma nota no período selecionado.';
    corpo.innerHTML = `<tr><td colspan="8" class="vazio">${msg}</td></tr>`;
    document.getElementById('sel-todas').checked = false;
    atualizarBotaoBaixar();
    return;
  }

  corpo.innerHTML = visiveis.map(n => {
    const emitida = notaEhEmitida(n);
    return `
    <tr>
      <td class="col-sel"><input type="checkbox" class="sel-nota" value="${n.id}"></td>
      <td class="chave">${n.chave_nfe || '—'}</td>
      <td class="mono">${ufDaChave(n.chave_nfe)}</td>
      <td>
        ${n.emitente_nome ? escapeHtml(n.emitente_nome) : '<span class="mono">nota completa</span>'}
        ${n.emitente_cnpj ? `<br><span class="mono">${formatarCnpj(n.emitente_cnpj)}</span>` : ''}
        <br><span class="badge-origem ${emitida ? 'emitida' : 'recebida'}">${emitida ? 'emitida' : 'recebida'}</span>
      </td>
      <td class="mono">${formatarData(n.data_emissao)}</td>
      <td>${n.valor_total != null ? formatarMoeda(n.valor_total) : '—'}</td>
      <td><span class="badge-situacao">${descreverSituacao(n)}</span></td>
      <td><span class="link-baixar" onclick="baixarXml(${n.id})">baixar XML</span></td>
    </tr>
  `;
  }).join('');

  document.getElementById('sel-todas').checked = false;
  atualizarBotaoBaixar();
}

// ---------------- Seleção múltipla + download em lote ----------------

function atualizarBotaoBaixar() {
  const qtd = document.querySelectorAll('.sel-nota:checked').length;
  const btn = document.getElementById('btn-baixar-sel');
  btn.disabled = qtd === 0;
  btn.textContent = qtd > 0 ? `Baixar ${qtd} XML(s)` : 'Baixar selecionadas';
}

document.getElementById('sel-todas').onchange = (ev) => {
  document.querySelectorAll('.sel-nota').forEach(cb => { cb.checked = ev.target.checked; });
  atualizarBotaoBaixar();
};

document.getElementById('corpo-tabela-notas').addEventListener('change', (ev) => {
  if (ev.target.classList.contains('sel-nota')) atualizarBotaoBaixar();
});

document.getElementById('btn-baixar-sel').onclick = async () => {
  const ids = Array.from(document.querySelectorAll('.sel-nota:checked')).map(cb => Number(cb.value));
  if (!ids.length) return;

  const st = document.getElementById('status-download');
  st.className = 'status-consulta';
  st.textContent = 'Salvando…';

  const r = await api().baixar_xmls(ids);
  if (r.ok) {
    st.className = 'status-consulta ok';
    st.textContent = `${r.salvos} XML(s) salvos em ${r.pasta}`;
  } else if (r.erro) {
    st.className = 'status-consulta erro';
    st.textContent = r.erro;
  } else {
    st.textContent = ''; // usuário cancelou o diálogo de pasta
  }
};

// Código cSitNFe do resumo vira texto legível; nota completa mostra "completa".
function descreverSituacao(n) {
  const mapa = { '1': 'autorizada', '2': 'denegada', '3': 'cancelada' };
  return mapa[n.situacao] || n.situacao || '—';
}

async function baixarXml(notaId) {
  const r = await api().baixar_xml(notaId);
  if (r.ok) {
    // silencioso — o diálogo nativo já confirma visualmente
  }
}

// ---------------- Nova empresa: cadastro + validação de certificado ----------------

document.getElementById('btn-nova-empresa').onclick = () => {
  document.getElementById('form-cadastro').reset();
  document.getElementById('f-cert-path').value = '';
  certValidado = null;
  document.getElementById('cert-status').classList.add('hidden');
  document.getElementById('btn-salvar-empresa').disabled = true;
  document.querySelectorAll('.item-empresa').forEach(el => el.classList.remove('ativo'));
  mostrarTela('tela-cadastro');
};

document.getElementById('btn-escolher-cert').onclick = async () => {
  const r = await api().escolher_arquivo_certificado();
  if (r.ok) {
    document.getElementById('f-cert-path').value = r.path;
    certValidado = null;
    document.getElementById('btn-salvar-empresa').disabled = true;
    document.getElementById('cert-status').classList.add('hidden');
  }
};

document.getElementById('btn-validar-cert').onclick = async () => {
  const path = document.getElementById('f-cert-path').value;
  const senha = document.getElementById('f-cert-senha').value;
  const statusEl = document.getElementById('cert-status');

  if (!path) { alert('Escolha o arquivo .pfx primeiro.'); return; }
  if (!senha) { alert('Digite a senha do certificado.'); return; }

  statusEl.className = 'cert-status';
  statusEl.classList.remove('hidden');
  statusEl.textContent = 'Validando…';

  const r = await api().validar_certificado(path, senha);

  if (r.ok) {
    const ufDetectada = r.uf && CODIGO_POR_UF[r.uf] ? r.uf : null;
    statusEl.className = 'cert-status ok';
    statusEl.textContent =
      `Certificado válido.\nTitular: ${r.titular}\n` +
      (r.cnpj ? `CNPJ identificado: ${formatarCnpj(r.cnpj)}\n` : 'CNPJ não identificado automaticamente — confira manualmente.\n') +
      (ufDetectada ? `Estado detectado: ${ufDetectada}\n` : '') +
      `Validade até: ${formatarData(r.validade_fim)}`;
    certValidado = { path };
    document.getElementById('btn-salvar-empresa').disabled = false;
    if (r.cnpj && !document.getElementById('f-cnpj').value) {
      document.getElementById('f-cnpj').value = formatarCnpj(r.cnpj);
    }
    if (ufDetectada) {
      document.getElementById('f-uf').value = CODIGO_POR_UF[ufDetectada];
    }
  } else {
    statusEl.className = 'cert-status erro';
    statusEl.textContent = r.erro;
    certValidado = null;
    document.getElementById('btn-salvar-empresa').disabled = true;
  }
};

document.getElementById('form-cadastro').onsubmit = async (ev) => {
  ev.preventDefault();
  if (!certValidado) return;

  const razao = document.getElementById('f-razao').value.trim();
  const cnpj = document.getElementById('f-cnpj').value.trim();
  const ambiente = document.getElementById('f-ambiente').value;
  const uf = document.getElementById('f-uf').value.trim() || '35';
  const senha = document.getElementById('f-cert-senha').value;

  // Reaproveita a senha já validada agora, pra nao pedir de novo na primeira consulta.
  const r = await api().cadastrar_empresa(razao, cnpj, certValidado.path, ambiente, uf, senha);
  if (r.ok) {
    await carregarEmpresas(r.empresa_id);
  }
};

// ---------------- Senha do certificado (pedida 1x por sessão, fica só em memória) ----------------

function pedirSenha(mensagemErro) {
  const modal = document.getElementById('modal-senha');
  const input = document.getElementById('modal-senha-input');
  const erroEl = document.getElementById('modal-senha-erro');

  input.value = '';
  if (mensagemErro) {
    erroEl.textContent = mensagemErro;
    erroEl.classList.remove('hidden');
  } else {
    erroEl.classList.add('hidden');
  }

  modal.classList.remove('hidden');
  input.focus();

  return new Promise((resolve) => {
    function limpar() {
      modal.classList.add('hidden');
      document.getElementById('modal-senha-confirmar').onclick = null;
      document.getElementById('modal-senha-cancelar').onclick = null;
      input.onkeydown = null;
    }
    document.getElementById('modal-senha-confirmar').onclick = () => {
      const v = input.value;
      limpar();
      resolve(v || null);
    };
    document.getElementById('modal-senha-cancelar').onclick = () => {
      limpar();
      resolve(null);
    };
    input.onkeydown = (ev) => {
      if (ev.key === 'Enter') document.getElementById('modal-senha-confirmar').click();
      if (ev.key === 'Escape') document.getElementById('modal-senha-cancelar').click();
    };
  });
}

// ---------------- Consulta de notas ----------------

document.getElementById('btn-consultar').onclick = () => executarConsulta(false);

document.getElementById('btn-resync').onclick = async () => {
  if (!empresaAtual) return;
  const ok = confirm(
    'Re-sincronizar do zero?\n\n' +
    'O app vai pedir à SEFAZ TODOS os documentos que ela ainda guarda deste CNPJ ' +
    '(últimos ~90 dias). As notas já salvas não são apagadas nem duplicadas.\n\n' +
    'Use isso quando uma nota conhecida não apareceu na lista.\n\n' +
    'Se a janela de 1 hora da SEFAZ ainda estiver correndo, a re-sincronização ' +
    'fica agendada e roda assim que liberar.'
  );
  if (!ok) return;

  const statusEl = document.getElementById('status-consulta');
  const r = await api().resincronizar(empresaAtual.id);

  // A janela de 1h ainda está correndo. NÃO forçar aqui: a SEFAZ responderia
  // 656 e o próprio 656 reiniciaria o bloqueio, prendendo o usuário num laço.
  // O cursor já está zerado, então a próxima busca permitida re-baixa tudo.
  if (r.minutos_espera > 0) {
    statusEl.className = 'status-consulta';
    statusEl.textContent =
      `Re-sincronização agendada: a próxima busca deste CNPJ vai re-baixar tudo ` +
      `que a SEFAZ ainda guarda (~90 dias). Falta ~${r.minutos_espera} min para a ` +
      `janela de 1 hora liberar.`;
    await atualizarEstadoConsulta();
    return;
  }

  executarConsulta(false);
};

async function executarConsulta(forcar) {
  const statusEl = document.getElementById('status-consulta');
  const logEl = document.getElementById('log-consulta');

  if (!empresaAtual) return;

  const btn = document.getElementById('btn-consultar');
  btn.disabled = true;
  statusEl.className = 'status-consulta';
  statusEl.textContent = 'Consultando SEFAZ…';
  logEl.classList.remove('hidden');
  logEl.textContent = '';

  let senhaDigitadaAgora = null;
  let totalNovos = 0;
  let totalEventos = 0;
  let seguir = true;
  let rodadas = 0;

  while (seguir && rodadas < 10) { // trava de segurança: no máx. 10 lotes por clique
    rodadas++;
    // "forcar" só vale pro primeiro lote: se ele voltar 138, os lotes
    // seguintes já passam pela checagem normalmente (o log vira 138).
    let r = await api().consultar_notas(empresaAtual.id, senhaDigitadaAgora, forcar && rodadas === 1);
    senhaDigitadaAgora = null; // já foi usada/cacheada no backend; não reenvia à toa

    if (!r.ok && r.senha_necessaria) {
      const senha = await pedirSenha(r.erro && r.erro !== 'Digite a senha do certificado.' ? r.erro : null);
      rodadas--; // essa rodada não contou de verdade, foi só pedir a senha
      if (!senha) {
        statusEl.className = 'status-consulta';
        statusEl.textContent = 'Consulta cancelada.';
        seguir = false;
        break;
      }
      senhaDigitadaAgora = senha;
      continue;
    }

    if (!r.ok) {
      // "aguardar" = regra de 1h da SEFAZ, não é falha: mostra como aviso neutro
      statusEl.className = r.aguardar ? 'status-consulta' : 'status-consulta erro';
      statusEl.textContent = r.erro;
      logEl.textContent += `${r.aguardar ? '[aviso]' : '[erro]'} ${r.erro}\n`;
      seguir = false;
      break;
    }

    totalNovos += r.novos_documentos;
    totalEventos += (r.eventos || 0);
    logEl.textContent += `Lote ${rodadas}: cStat=${r.cstat} (${r.xmotivo}) — ` +
      `${r.novos_documentos} nota(s)` +
      (r.eventos ? `, ${r.eventos} evento(s) de nota (cancelamento/correção — não viram linha na lista)` : '') +
      (r.outros ? `, ${r.outros} outro(s) documento(s)` : '') +
      `. NSU ${r.ult_nsu} de ${r.max_nsu}\n`;
    if (r.nsu_pulados > 0) {
      logEl.textContent += `[ATENÇÃO] A SEFAZ avançou ${r.nsu_pulados} número(s) de sequência sem entregar documentos. ` +
        `Se uma nota conhecida não apareceu na lista, clique em "↻ Re-sincronizar do zero" — ` +
        `a re-busca completa roda assim que a janela de 1 hora liberar.\n`;
    }
    seguir = r.tem_mais;
  }

  btn.disabled = false;
  if (statusEl.textContent === 'Consultando SEFAZ…') {
    statusEl.className = 'status-consulta ok';
    if (totalNovos > 0) {
      statusEl.textContent = `${totalNovos} nota(s) nova(s) baixada(s).`;
    } else if (totalEventos > 0) {
      statusEl.textContent = `Nenhuma nota nova, mas a SEFAZ enviou ${totalEventos} evento(s) de notas (cancelamentos/correções).`;
    } else {
      statusEl.textContent = 'Nenhuma nota nova — a SEFAZ não tem documentos pendentes pra este CNPJ (ela só distribui os últimos ~90 dias).';
    }
  }

  await recarregarNotas(empresaAtual.id);
  await atualizarEstadoConsulta(); // se a consulta terminou em "nada novo", já mostra o contador de 1h
}

document.getElementById('btn-excluir-empresa').onclick = async () => {
  if (!empresaAtual) return;
  if (!confirm(`Remover "${empresaAtual.razao_social}" e todas as notas baixadas dela do app?`)) return;
  await api().excluir_empresa(empresaAtual.id);
  empresaAtual = null;
  mostrarTela('tela-vazia');
  await carregarEmpresas();
};

// ---------------- Utilitários ----------------

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function formatarCnpj(cnpj) {
  if (!cnpj) return '—';
  const d = cnpj.replace(/\D/g, '');
  if (d.length !== 14) return cnpj;
  return `${d.slice(0,2)}.${d.slice(2,5)}.${d.slice(5,8)}/${d.slice(8,12)}-${d.slice(12,14)}`;
}

function formatarMoeda(v) {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function formatarData(iso) {
  if (!iso) return '—';
  try {
    const dt = new Date(iso);
    if (isNaN(dt)) return iso;
    return dt.toLocaleString('pt-BR');
  } catch { return iso; }
}

// ---------------- Boot ----------------

window.addEventListener('pywebviewready', () => {
  carregarEmpresas();
});
