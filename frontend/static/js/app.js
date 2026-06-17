const API_BASE = '/api';

// Estado de edição
let editandoContato = null;
let editandoOportunidade = null;
let editandoTarefa = null;
let editandoTarefaConcluida = false;

// Navegação
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', e => {
        e.preventDefault();
        const page = item.getAttribute('data-page');
        document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
        item.classList.add('active');
        document.querySelectorAll('.page-content').forEach(p => p.classList.remove('active'));
        document.getElementById(`page-${page}`).classList.add('active');
        const titles = {
            dashboard: 'Dashboard', contatos: 'Contatos', oportunidades: 'Oportunidades',
            tarefas: 'Tarefas', calendario: 'Calendário', relatorios: 'Relatórios', whatsapp: 'WhatsApp'
        };
        document.getElementById('page-title').textContent = titles[page];
        loadPageData(page);
    });
});

// Modais
function openModal(id) { document.getElementById(id).classList.add('active'); }

function closeModal(id) {
    document.getElementById(id).classList.remove('active');
    if (id === 'modal-contato') {
        editandoContato = null;
        document.getElementById('modal-contato-title').textContent = 'Novo Contato';
        document.getElementById('form-contato').reset();
    } else if (id === 'modal-oportunidade') {
        editandoOportunidade = null;
        document.getElementById('modal-oportunidade-title').textContent = 'Nova Oportunidade';
        document.getElementById('form-oportunidade').reset();
    } else if (id === 'modal-tarefa') {
        editandoTarefa = null;
        editandoTarefaConcluida = false;
        document.getElementById('modal-tarefa-title').textContent = 'Nova Tarefa';
        document.getElementById('form-tarefa').reset();
    }
}

// Fecha modal ao clicar no backdrop
window.onclick = e => {
    if (e.target.classList.contains('modal')) closeModal(e.target.id);
};

// Funções de abertura com estado limpo
function abrirNovoContato() {
    closeModal('modal-contato');
    openModal('modal-contato');
}
function abrirNovaOportunidade() {
    closeModal('modal-oportunidade');
    openModal('modal-oportunidade');
}
function abrirNovaTarefa() {
    closeModal('modal-tarefa');
    openModal('modal-tarefa');
}

// API helper
async function apiCall(endpoint, options = {}) {
    try {
        const r = await fetch(`${API_BASE}${endpoint}`, {
            headers: { 'Content-Type': 'application/json' },
            ...options
        });
        if (!r.ok) {
            const errorText = await r.text();
            throw new Error(`Erro ${r.status}: ${errorText}`);
        }
        return await r.json();
    } catch (err) {
        console.error('[API Error]', err);
        showToast(err.message, 'error');
        throw err;
    }
}

function showToast(msg, type = 'success') {
    const t = document.createElement('div');
    t.className = 'toast';
    if (type === 'error') t.style.background = '#ef4444';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => {
        t.style.animation = 'slideIn .3s ease reverse';
        setTimeout(() => t.remove(), 300);
    }, 3000);
}

function formatDateTimeLocal(dt) {
    if (!dt) return '';
    return new Date(dt).toISOString().slice(0, 16);
}

function formatDateLocal(dt) {
    if (!dt) return '';
    return new Date(dt).toISOString().slice(0, 10);
}

function cleanFormData(rawData) {
    const data = {};
    for (const [key, value] of Object.entries(rawData)) {
        data[key] = value === '' ? null : value;
    }
    return data;
}

// ===================== CONTATOS =====================

async function salvarContato(e) {
    e.preventDefault();
    const data = cleanFormData(Object.fromEntries(new FormData(e.target)));
    try {
        if (editandoContato) {
            await apiCall(`/contatos/${editandoContato}`, { method: 'PUT', body: JSON.stringify(data) });
            showToast('Contato atualizado!');
        } else {
            await apiCall('/contatos', { method: 'POST', body: JSON.stringify(data) });
            showToast('Contato salvo com sucesso!');
        }
        closeModal('modal-contato');
        carregarContatos();
        carregarDashboard();
    } catch (err) {
        console.error('Erro ao salvar contato:', err);
    }
}

async function editarContato(id) {
    try {
        const c = await apiCall(`/contatos/${id}`);
        editandoContato = id;
        document.getElementById('modal-contato-title').textContent = 'Editar Contato';
        const form = document.getElementById('form-contato');
        form.reset();
        ['nome', 'empresa', 'email', 'telefone', 'whatsapp', 'cargo', 'cidade', 'estado', 'origem', 'status', 'tags', 'notas'].forEach(f => {
            if (form.elements[f] !== undefined) form.elements[f].value = c[f] || '';
        });
        openModal('modal-contato');
    } catch (err) {
        console.error(err);
    }
}

async function carregarContatos() {
    try {
        const contatos = await apiCall('/contatos');
        const tbody = document.getElementById('tbody-contatos');
        if (!contatos || contatos.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-state"><i class="fas fa-users"></i><p>Nenhum contato encontrado</p></td></tr>';
            return;
        }
        tbody.innerHTML = contatos.map(c => `
            <tr>
                <td><strong>${c.nome}</strong></td>
                <td>${c.empresa || '-'}</td>
                <td>${c.email || '-'}</td>
                <td>${c.telefone || '-'}</td>
                <td><span class="status-badge status-${(c.status || 'lead').toLowerCase()}">${c.status || 'Lead'}</span></td>
                <td>
                    <button class="action-btn edit" onclick="editarContato(${c.id})" title="Editar"><i class="fas fa-edit"></i></button>
                    <button class="action-btn delete" onclick="deletarContato(${c.id})" title="Excluir"><i class="fas fa-trash"></i></button>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        console.error('Erro ao carregar contatos:', err);
    }
}

async function deletarContato(id) {
    if (!confirm('Tem certeza que deseja deletar este contato?')) return;
    try {
        await apiCall(`/contatos/${id}`, { method: 'DELETE' });
        showToast('Contato deletado!');
        carregarContatos();
        carregarDashboard();
    } catch (err) {
        console.error(err);
    }
}

// ===================== OPORTUNIDADES =====================

async function salvarOportunidade(e) {
    e.preventDefault();
    const data = cleanFormData(Object.fromEntries(new FormData(e.target)));
    data.valor = parseFloat(data.valor) || 0;
    data.probabilidade = parseInt(data.probabilidade) || 10;
    try {
        if (editandoOportunidade) {
            await apiCall(`/oportunidades/${editandoOportunidade}`, { method: 'PUT', body: JSON.stringify(data) });
            showToast('Oportunidade atualizada!');
        } else {
            await apiCall('/oportunidades', { method: 'POST', body: JSON.stringify(data) });
            showToast('Oportunidade criada!');
        }
        closeModal('modal-oportunidade');
        carregarOportunidades();
        carregarDashboard();
    } catch (err) {
        console.error(err);
    }
}

async function editarOportunidade(id) {
    try {
        const o = await apiCall(`/oportunidades/${id}`);
        editandoOportunidade = id;
        document.getElementById('modal-oportunidade-title').textContent = 'Editar Oportunidade';
        const form = document.getElementById('form-oportunidade');
        form.reset();
        form.elements['titulo'].value = o.titulo || '';
        form.elements['valor'].value = o.valor || 0;
        form.elements['etapa'].value = o.etapa || 'Prospecção';
        form.elements['probabilidade'].value = o.probabilidade || 10;
        form.elements['data_prevista_fechamento'].value = formatDateLocal(o.data_prevista_fechamento);
        form.elements['descricao'].value = o.descricao || '';
        openModal('modal-oportunidade');
    } catch (err) {
        console.error(err);
    }
}

async function carregarOportunidades() {
    try {
        const ops = await apiCall('/oportunidades');
        const kanban = document.getElementById('kanban-oportunidades');
        const etapas = ['Prospecção', 'Qualificação', 'Proposta', 'Negociação', 'Fechamento'];

        kanban.innerHTML = etapas.map(etapa => {
            const eOps = ops.filter(o => o.etapa === etapa);
            return `<div class="kanban-column" data-etapa="${etapa}">
                <div class="kanban-column-header"><h4>${etapa}</h4><span class="kanban-count">${eOps.length}</span></div>
                ${!eOps.length
                    ? '<div class="empty-state" style="padding:20px;"><i class="fas fa-inbox"></i><p>Vazio</p></div>'
                    : eOps.map(o => `
                        <div class="kanban-card" draggable="true" data-id="${o.id}">
                            <div class="kanban-card-title">${o.titulo}</div>
                            <div class="kanban-card-valor">R$ ${o.valor.toLocaleString('pt-BR')}</div>
                            <div class="kanban-card-meta">
                                <span>Prob: ${o.probabilidade}%</span>
                                <div>
                                    <button class="action-btn edit" onclick="editarOportunidade(${o.id})" style="padding:2px 6px;" title="Editar"><i class="fas fa-edit"></i></button>
                                    <button class="action-btn delete" onclick="deletarOportunidade(${o.id})" style="padding:2px 6px;" title="Excluir"><i class="fas fa-trash"></i></button>
                                </div>
                            </div>
                            <div class="probability-bar"><div class="probability-fill" style="width:${o.probabilidade}%"></div></div>
                        </div>`).join('')}
            </div>`;
        }).join('');

        setupDragAndDrop();
    } catch (err) {
        console.error(err);
    }
}

async function deletarOportunidade(id) {
    if (!confirm('Deletar esta oportunidade?')) return;
    try {
        await apiCall(`/oportunidades/${id}`, { method: 'DELETE' });
        showToast('Deletada!');
        carregarOportunidades();
        carregarDashboard();
    } catch (err) {
        console.error(err);
    }
}

function setupDragAndDrop() {
    document.querySelectorAll('.kanban-card').forEach(card => {
        card.addEventListener('dragstart', e => {
            e.dataTransfer.setData('text/plain', card.dataset.id);
            setTimeout(() => card.classList.add('dragging'), 0);
        });
        card.addEventListener('dragend', () => {
            card.classList.remove('dragging');
        });
    });

    document.querySelectorAll('.kanban-column').forEach(col => {
        col.addEventListener('dragover', e => {
            e.preventDefault();
            col.classList.add('drag-over');
        });
        col.addEventListener('dragleave', e => {
            if (!col.contains(e.relatedTarget)) col.classList.remove('drag-over');
        });
        col.addEventListener('drop', async e => {
            e.preventDefault();
            col.classList.remove('drag-over');
            const id = e.dataTransfer.getData('text/plain');
            if (!id) return;
            const novaEtapa = col.dataset.etapa;
            try {
                const op = await apiCall(`/oportunidades/${id}`);
                await apiCall(`/oportunidades/${id}`, {
                    method: 'PUT',
                    body: JSON.stringify({ ...op, etapa: novaEtapa })
                });
                carregarOportunidades();
                carregarDashboard();
                showToast(`Movido para ${novaEtapa}!`);
            } catch (err) {
                console.error(err);
            }
        });
    });
}

// ===================== TAREFAS =====================

async function salvarTarefa(e) {
    e.preventDefault();
    const data = cleanFormData(Object.fromEntries(new FormData(e.target)));
    try {
        if (editandoTarefa) {
            data.concluida = editandoTarefaConcluida;
            await apiCall(`/tarefas/${editandoTarefa}`, { method: 'PUT', body: JSON.stringify(data) });
            showToast('Tarefa atualizada!');
        } else {
            await apiCall('/tarefas', { method: 'POST', body: JSON.stringify(data) });
            showToast('Tarefa criada!');
        }
        closeModal('modal-tarefa');
        carregarTarefas();
        carregarDashboard();
    } catch (err) {
        console.error(err);
    }
}

async function editarTarefa(id) {
    try {
        const t = await apiCall(`/tarefas/${id}`);
        editandoTarefa = id;
        editandoTarefaConcluida = t.concluida;
        document.getElementById('modal-tarefa-title').textContent = 'Editar Tarefa';
        const form = document.getElementById('form-tarefa');
        form.reset();
        form.elements['titulo'].value = t.titulo || '';
        form.elements['tipo'].value = t.tipo || 'Ligação';
        form.elements['prioridade'].value = t.prioridade || 'Normal';
        form.elements['data_hora'].value = formatDateTimeLocal(t.data_hora);
        form.elements['descricao'].value = t.descricao || '';
        openModal('modal-tarefa');
    } catch (err) {
        console.error(err);
    }
}

async function carregarTarefas() {
    try {
        const tarefas = await apiCall('/tarefas');
        const tbody = document.getElementById('tbody-tarefas');
        if (!tarefas || tarefas.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-state"><i class="fas fa-tasks"></i><p>Nenhuma tarefa</p></td></tr>';
            return;
        }
        tbody.innerHTML = tarefas.map(t => `
            <tr>
                <td><input type="checkbox" class="task-checkbox" ${t.concluida ? 'checked' : ''} onchange="concluirTarefa(${t.id}, this.checked)"></td>
                <td style="${t.concluida ? 'text-decoration:line-through;color:#9ca3af;' : ''}"><strong>${t.titulo}</strong></td>
                <td>${t.tipo || '-'}</td>
                <td>${t.data_hora ? new Date(t.data_hora).toLocaleString('pt-BR') : '-'}</td>
                <td><span class="priority-badge priority-${(t.prioridade || 'normal').toLowerCase()}">${t.prioridade || 'Normal'}</span></td>
                <td>
                    <button class="action-btn edit" onclick="editarTarefa(${t.id})" title="Editar"><i class="fas fa-edit"></i></button>
                    <button class="action-btn delete" onclick="deletarTarefa(${t.id})" title="Excluir"><i class="fas fa-trash"></i></button>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        console.error(err);
    }
}

async function concluirTarefa(id, ok) {
    if (!ok) return;
    try {
        await apiCall(`/tarefas/${id}/concluir`, { method: 'PUT' });
        showToast('Tarefa concluída!');
        carregarTarefas();
        carregarDashboard();
    } catch (err) {
        console.error(err);
    }
}

async function deletarTarefa(id) {
    if (!confirm('Deletar esta tarefa?')) return;
    try {
        await apiCall(`/tarefas/${id}`, { method: 'DELETE' });
        showToast('Deletada!');
        carregarTarefas();
        carregarDashboard();
    } catch (err) {
        console.error(err);
    }
}

// ===================== DASHBOARD =====================

async function carregarDashboard() {
    try {
        const r = await apiCall('/relatorios/resumo');
        document.getElementById('stat-contatos').textContent = r.total_contatos;
        document.getElementById('stat-oportunidades').textContent = r.total_oportunidades;
        document.getElementById('stat-tarefas').textContent = r.tarefas_pendentes;
        document.getElementById('stat-valor').textContent = `R$ ${r.valor_ponderado.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        document.getElementById('rel-receita').textContent = document.getElementById('stat-valor').textContent;

        const f = await apiCall('/relatorios/funil');
        renderFunil(f.etapas);
        await renderTarefasPendentes();
    } catch (err) {
        console.error(err);
    }
}

function renderFunil(etapas) {
    const c = document.getElementById('funil-vendas');
    if (!etapas || etapas.length === 0) {
        c.innerHTML = '<div class="empty-state"><i class="fas fa-chart-pie"></i><p>Sem dados</p></div>';
        return;
    }
    const mx = Math.max(...etapas.map(e => e.quantidade));
    c.innerHTML = etapas.map(e => `
        <div class="funnel-stage">
            <div class="funnel-label">${e.etapa}</div>
            <div class="funnel-count">${e.quantidade}</div>
            <div class="funnel-bar"><div class="funnel-bar-fill" style="width:${(e.quantidade / mx) * 100}%"></div></div>
        </div>
    `).join('');
}

async function renderTarefasPendentes() {
    try {
        const ts = await apiCall('/tarefas');
        const ps = ts.filter(t => !t.concluida).slice(0, 5);
        const c = document.getElementById('tarefas-pendentes');
        if (!ps.length) {
            c.innerHTML = '<div class="empty-state"><i class="fas fa-check-circle"></i><p>Tudo feito!</p></div>';
            return;
        }
        c.innerHTML = ps.map(t => `
            <div class="task-item">
                <input type="checkbox" class="task-checkbox" onchange="concluirTarefa(${t.id}, this.checked)">
                <div class="task-info">
                    <div class="task-title">${t.titulo}</div>
                    <div class="task-meta">${t.tipo || ''} ${t.data_hora ? '• ' + new Date(t.data_hora).toLocaleDateString('pt-BR') : ''}</div>
                </div>
                <span class="priority-badge priority-${(t.prioridade || 'normal').toLowerCase()}">${t.prioridade || 'Normal'}</span>
            </div>
        `).join('');
    } catch (err) {
        console.error(err);
    }
}

// ===================== CALENDÁRIO =====================

function renderizarCalendario() {
    const grid = document.getElementById('calendar-grid');
    grid.innerHTML = '';
    const today = new Date(), ano = today.getFullYear(), mes = today.getMonth();
    const primeiroDia = new Date(ano, mes, 1).getDay(), ultimoDia = new Date(ano, mes + 1, 0).getDate();
    document.getElementById('cal-title').textContent = today.toLocaleDateString('pt-BR', { month: 'long', year: 'numeric' }).replace(/^\w/, c => c.toUpperCase());

    ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'].forEach(d => {
        const el = document.createElement('div');
        el.className = 'calendar-day-header';
        el.textContent = d;
        grid.appendChild(el);
    });

    for (let i = 0; i < primeiroDia; i++) {
        const el = document.createElement('div');
        el.className = 'calendar-day';
        grid.appendChild(el);
    }
    for (let d = 1; d <= ultimoDia; d++) {
        const el = document.createElement('div');
        el.className = 'calendar-day';
        el.textContent = d;
        if (d === today.getDate()) el.classList.add('today');
        grid.appendChild(el);
    }
}

// Busca
document.getElementById('search-contatos')?.addEventListener('input', e => {
    const termo = e.target.value.toLowerCase();
    document.querySelectorAll('#tbody-contatos tr').forEach(r => {
        r.style.display = r.textContent.toLowerCase().includes(termo) ? '' : 'none';
    });
});

function loadPageData(page) {
    switch (page) {
        case 'dashboard': carregarDashboard(); break;
        case 'contatos': carregarContatos(); break;
        case 'oportunidades': carregarOportunidades(); break;
        case 'tarefas': carregarTarefas(); break;
        case 'calendario': renderizarCalendario(); break;
        case 'relatorios': carregarDashboard(); break;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    carregarDashboard();
});
