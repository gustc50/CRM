#!/usr/bin/env python3
"""CRM Brasileiro - Gerador Automático (Versão 3.1 - Com Edição e Kanban Corrigido)"""
import os
import sys
import subprocess

def write_file(path, content):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  ✓ {path}")

def criar_projeto():
    print("\n🚀 CRM BRASILEIRO - GERADOR COMPLETO (v3.1)\n")

    write_file("requirements.txt",
        "fastapi>=0.115.0\n"
        "uvicorn[standard]>=0.32.0\n"
        "sqlalchemy>=2.0.36\n"
        "pydantic[email]>=2.10.0\n"
        "python-multipart>=0.0.17\n"
    )

    write_file("backend/__init__.py", "# CRM Brasileiro Backend\n")

    write_file("backend/main.py", '''\
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Float, Boolean, func
from sqlalchemy.orm import sessionmaker, relationship, Session, declarative_base
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")
DATABASE_URL = f"sqlite:///{os.path.join(BASE_DIR, 'crm_brasileiro.db')}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, index=True)
    senha = Column(String(255))
    cargo = Column(String(50), default="Vendedor")
    ativo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    contatos = relationship("Contato", back_populates="responsavel")
    tarefas = relationship("Tarefa", back_populates="responsavel")

class Contato(Base):
    __tablename__ = "contatos"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(200), nullable=False)
    email = Column(String(100), index=True)
    telefone = Column(String(20))
    whatsapp = Column(String(20))
    empresa = Column(String(200))
    cargo = Column(String(100))
    cidade = Column(String(100))
    estado = Column(String(50))
    notas = Column(Text)
    origem = Column(String(100))
    tags = Column(String(500))
    status = Column(String(50), default="Lead")
    responsavel_id = Column(Integer, ForeignKey("usuarios.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    responsavel = relationship("Usuario", back_populates="contatos")
    interacoes = relationship("Interacao", back_populates="contato")
    oportunidades = relationship("Oportunidade", back_populates="contato")

class Oportunidade(Base):
    __tablename__ = "oportunidades"
    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String(200), nullable=False)
    valor = Column(Float, default=0.0)
    etapa = Column(String(100), default="Prospecção")
    probabilidade = Column(Integer, default=10)
    data_prevista_fechamento = Column(DateTime)
    descricao = Column(Text)
    contato_id = Column(Integer, ForeignKey("contatos.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    contato = relationship("Contato", back_populates="oportunidades")

class Tarefa(Base):
    __tablename__ = "tarefas"
    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String(200), nullable=False)
    descricao = Column(Text)
    tipo = Column(String(50))
    data_hora = Column(DateTime)
    concluida = Column(Boolean, default=False)
    prioridade = Column(String(20), default="Normal")
    responsavel_id = Column(Integer, ForeignKey("usuarios.id"))
    oportunidade_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    responsavel = relationship("Usuario", back_populates="tarefas")

class Interacao(Base):
    __tablename__ = "interacoes"
    id = Column(Integer, primary_key=True, index=True)
    tipo = Column(String(50))
    resumo = Column(Text)
    detalhes = Column(Text)
    data_hora = Column(DateTime, default=datetime.utcnow)
    contato_id = Column(Integer, ForeignKey("contatos.id"))
    contato = relationship("Contato", back_populates="interacoes")

Base.metadata.create_all(bind=engine)

app = FastAPI(title="CRM Brasileiro", version="3.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

static_dir = os.path.join(FRONTEND_DIR, "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

class ContatoBase(BaseModel):
    nome: str
    email: Optional[str] = None
    telefone: Optional[str] = None
    whatsapp: Optional[str] = None
    empresa: Optional[str] = None
    cargo: Optional[str] = None
    cidade: Optional[str] = None
    estado: Optional[str] = None
    notas: Optional[str] = None
    origem: Optional[str] = None
    tags: Optional[str] = None
    status: str = "Lead"
    responsavel_id: Optional[int] = None

class ContatoCreate(ContatoBase): pass
class ContatoResponse(ContatoBase):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}

class OportunidadeBase(BaseModel):
    titulo: str
    valor: float = 0.0
    etapa: str = "Prospecção"
    probabilidade: int = 10
    data_prevista_fechamento: Optional[datetime] = None
    descricao: Optional[str] = None
    contato_id: Optional[int] = None

class OportunidadeCreate(OportunidadeBase): pass
class OportunidadeResponse(OportunidadeBase):
    id: int
    created_at: datetime
    model_config = {"from_attributes": True}

class TarefaBase(BaseModel):
    titulo: str
    descricao: Optional[str] = None
    tipo: Optional[str] = None
    data_hora: Optional[datetime] = None
    concluida: bool = False
    prioridade: str = "Normal"
    responsavel_id: Optional[int] = None
    oportunidade_id: Optional[int] = None

class TarefaCreate(TarefaBase): pass
class TarefaResponse(TarefaBase):
    id: int
    created_at: datetime
    model_config = {"from_attributes": True}

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse(os.path.join(FRONTEND_DIR, "templates", "index.html"))

# --- Contatos ---

@app.post("/api/contatos", response_model=ContatoResponse)
async def create_contato(contato: ContatoCreate, db: Session = Depends(get_db)):
    db_obj = Contato(**contato.model_dump())
    db.add(db_obj); db.commit(); db.refresh(db_obj)
    return db_obj

@app.get("/api/contatos", response_model=list[ContatoResponse])
async def get_contatos(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Contato).offset(skip).limit(limit).all()

@app.get("/api/contatos/{cid}", response_model=ContatoResponse)
async def get_contato(cid: int, db: Session = Depends(get_db)):
    c = db.query(Contato).filter(Contato.id == cid).first()
    if not c: raise HTTPException(404, "Não encontrado")
    return c

@app.put("/api/contatos/{cid}", response_model=ContatoResponse)
async def update_contato(cid: int, data: ContatoCreate, db: Session = Depends(get_db)):
    c = db.query(Contato).filter(Contato.id == cid).first()
    if not c: raise HTTPException(404, "Não encontrado")
    for k, v in data.model_dump().items(): setattr(c, k, v)
    c.updated_at = datetime.utcnow()
    db.commit(); db.refresh(c)
    return c

@app.delete("/api/contatos/{cid}")
async def delete_contato(cid: int, db: Session = Depends(get_db)):
    c = db.query(Contato).filter(Contato.id == cid).first()
    if not c: raise HTTPException(404, "Não encontrado")
    db.delete(c); db.commit()
    return {"message": "Deletado"}

# --- Oportunidades ---

@app.post("/api/oportunidades", response_model=OportunidadeResponse)
async def create_op(op: OportunidadeCreate, db: Session = Depends(get_db)):
    db_obj = Oportunidade(**op.model_dump())
    db.add(db_obj); db.commit(); db.refresh(db_obj)
    return db_obj

@app.get("/api/oportunidades", response_model=list[OportunidadeResponse])
async def get_ops(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Oportunidade).offset(skip).limit(limit).all()

@app.get("/api/oportunidades/{oid}", response_model=OportunidadeResponse)
async def get_op(oid: int, db: Session = Depends(get_db)):
    o = db.query(Oportunidade).filter(Oportunidade.id == oid).first()
    if not o: raise HTTPException(404, "Não encontrada")
    return o

@app.put("/api/oportunidades/{oid}", response_model=OportunidadeResponse)
async def update_op(oid: int, data: OportunidadeCreate, db: Session = Depends(get_db)):
    o = db.query(Oportunidade).filter(Oportunidade.id == oid).first()
    if not o: raise HTTPException(404, "Não encontrada")
    for k, v in data.model_dump().items(): setattr(o, k, v)
    db.commit(); db.refresh(o)
    return o

@app.delete("/api/oportunidades/{oid}")
async def delete_op(oid: int, db: Session = Depends(get_db)):
    o = db.query(Oportunidade).filter(Oportunidade.id == oid).first()
    if not o: raise HTTPException(404, "Não encontrada")
    db.delete(o); db.commit()
    return {"message": "Deletada"}

# --- Tarefas ---

@app.post("/api/tarefas", response_model=TarefaResponse)
async def create_tarefa(t: TarefaCreate, db: Session = Depends(get_db)):
    db_obj = Tarefa(**t.model_dump())
    db.add(db_obj); db.commit(); db.refresh(db_obj)
    return db_obj

@app.get("/api/tarefas", response_model=list[TarefaResponse])
async def get_tarefas(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Tarefa).offset(skip).limit(limit).all()

@app.get("/api/tarefas/{tid}", response_model=TarefaResponse)
async def get_tarefa(tid: int, db: Session = Depends(get_db)):
    t = db.query(Tarefa).filter(Tarefa.id == tid).first()
    if not t: raise HTTPException(404, "Não encontrada")
    return t

@app.put("/api/tarefas/{tid}", response_model=TarefaResponse)
async def update_tarefa(tid: int, data: TarefaCreate, db: Session = Depends(get_db)):
    t = db.query(Tarefa).filter(Tarefa.id == tid).first()
    if not t: raise HTTPException(404, "Não encontrada")
    for k, v in data.model_dump().items(): setattr(t, k, v)
    db.commit(); db.refresh(t)
    return t

@app.put("/api/tarefas/{tid}/concluir", response_model=TarefaResponse)
async def concluir_tarefa(tid: int, db: Session = Depends(get_db)):
    t = db.query(Tarefa).filter(Tarefa.id == tid).first()
    if not t: raise HTTPException(404, "Não encontrada")
    t.concluida = True; db.commit(); db.refresh(t)
    return t

@app.delete("/api/tarefas/{tid}")
async def delete_tarefa(tid: int, db: Session = Depends(get_db)):
    t = db.query(Tarefa).filter(Tarefa.id == tid).first()
    if not t: raise HTTPException(404, "Não encontrada")
    db.delete(t); db.commit()
    return {"message": "Deletada"}

# --- Relatórios ---

@app.get("/api/relatorios/resumo")
async def relatorio_resumo(db: Session = Depends(get_db)):
    tc = db.query(Contato).count()
    to = db.query(Oportunidade).count()
    tp = db.query(Tarefa).filter(Tarefa.concluida == False).count()
    vals = db.query(Oportunidade).with_entities(Oportunidade.valor, Oportunidade.probabilidade).all()
    vp = sum((v.valor * v.probabilidade / 100) for v in vals if v.valor and v.probabilidade)
    return {"total_contatos": tc, "total_oportunidades": to, "tarefas_pendentes": tp, "valor_ponderado": vp or 0}

@app.get("/api/relatorios/funil")
async def relatorio_funil(db: Session = Depends(get_db)):
    etapas = db.query(Oportunidade.etapa, func.count(Oportunidade.id).label("qtd"), func.sum(Oportunidade.valor).label("val")).group_by(Oportunidade.etapa).all()
    return {"etapas": [{"etapa": e[0], "quantidade": e[1], "valor_total": e[2] or 0} for e in etapas]}

if __name__ == "__main__":
    import uvicorn
    print("\\n🚀 CRM Brasileiro iniciando...")
    print("📡 Acesse: http://localhost:8000\\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
''')

    write_file("frontend/templates/index.html", '''\
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CRM Brasileiro</title>
    <link rel="stylesheet" href="/static/css/style.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
</head>
<body>
    <div class="app-container">
        <aside class="sidebar">
            <div class="logo"><i class="fas fa-chart-line"></i><h1>CRM Brasil</h1></div>
            <nav class="nav-menu">
                <a href="#" class="nav-item active" data-page="dashboard"><i class="fas fa-tachometer-alt"></i><span>Dashboard</span></a>
                <a href="#" class="nav-item" data-page="contatos"><i class="fas fa-users"></i><span>Contatos</span></a>
                <a href="#" class="nav-item" data-page="oportunidades"><i class="fas fa-briefcase"></i><span>Oportunidades</span></a>
                <a href="#" class="nav-item" data-page="tarefas"><i class="fas fa-tasks"></i><span>Tarefas</span></a>
                <a href="#" class="nav-item" data-page="calendario"><i class="fas fa-calendar"></i><span>Calendário</span></a>
                <a href="#" class="nav-item" data-page="relatorios"><i class="fas fa-chart-bar"></i><span>Relatórios</span></a>
                <a href="#" class="nav-item" data-page="whatsapp"><i class="fab fa-whatsapp"></i><span>WhatsApp</span></a>
            </nav>
        </aside>
        <main class="main-content">
            <header class="header">
                <div class="header-left"><h2 id="page-title">Dashboard</h2></div>
                <div class="header-right">
                    <button class="btn-icon"><i class="fas fa-bell"></i><span class="badge">3</span></button>
                    <div class="user-menu"><i class="fas fa-user-circle"></i><span>Admin</span></div>
                </div>
            </header>
            <div id="page-dashboard" class="page-content active">
                <div class="stats-grid">
                    <div class="stat-card"><div class="stat-icon" style="background:#3b82f6;"><i class="fas fa-users"></i></div><div class="stat-info"><h3 id="stat-contatos">0</h3><p>Contatos</p></div></div>
                    <div class="stat-card"><div class="stat-icon" style="background:#10b981;"><i class="fas fa-briefcase"></i></div><div class="stat-info"><h3 id="stat-oportunidades">0</h3><p>Oportunidades</p></div></div>
                    <div class="stat-card"><div class="stat-icon" style="background:#f59e0b;"><i class="fas fa-tasks"></i></div><div class="stat-info"><h3 id="stat-tarefas">0</h3><p>Tarefas Pendentes</p></div></div>
                    <div class="stat-card"><div class="stat-icon" style="background:#ef4444;"><i class="fas fa-dollar-sign"></i></div><div class="stat-info"><h3 id="stat-valor">R$ 0</h3><p>Valor Ponderado</p></div></div>
                </div>
                <div class="content-grid">
                    <div class="card"><div class="card-header"><h3><i class="fas fa-chart-pie"></i> Funil de Vendas</h3></div><div class="card-body" id="funil-vendas"></div></div>
                    <div class="card"><div class="card-header"><h3><i class="fas fa-clock"></i> Tarefas Pendentes</h3></div><div class="card-body" id="tarefas-pendentes"></div></div>
                </div>
            </div>
            <div id="page-contatos" class="page-content">
                <div class="page-actions">
                    <button class="btn-primary" onclick="abrirNovoContato()"><i class="fas fa-plus"></i> Novo Contato</button>
                    <input type="text" class="search-input" placeholder="Buscar contatos..." id="search-contatos">
                </div>
                <div class="card">
                    <table class="data-table">
                        <thead><tr><th>Nome</th><th>Empresa</th><th>Email</th><th>Telefone</th><th>Status</th><th>Ações</th></tr></thead>
                        <tbody id="tbody-contatos"></tbody>
                    </table>
                </div>
            </div>
            <div id="page-oportunidades" class="page-content">
                <div class="page-actions"><button class="btn-primary" onclick="abrirNovaOportunidade()"><i class="fas fa-plus"></i> Nova Oportunidade</button></div>
                <div class="kanban-board" id="kanban-oportunidades"></div>
            </div>
            <div id="page-tarefas" class="page-content">
                <div class="page-actions"><button class="btn-primary" onclick="abrirNovaTarefa()"><i class="fas fa-plus"></i> Nova Tarefa</button></div>
                <div class="card">
                    <table class="data-table">
                        <thead><tr><th>Status</th><th>Título</th><th>Tipo</th><th>Data/Hora</th><th>Prioridade</th><th>Ações</th></tr></thead>
                        <tbody id="tbody-tarefas"></tbody>
                    </table>
                </div>
            </div>
            <div id="page-calendario" class="page-content">
                <div class="card">
                    <div class="calendar-header"><button class="btn-icon"><i class="fas fa-chevron-left"></i></button><h3 id="cal-title">Calendário</h3><button class="btn-icon"><i class="fas fa-chevron-right"></i></button></div>
                    <div class="calendar-grid" id="calendar-grid"></div>
                </div>
            </div>
            <div id="page-relatorios" class="page-content">
                <div class="stats-grid">
                    <div class="stat-card"><div class="stat-icon" style="background:#8b5cf6;"><i class="fas fa-chart-line"></i></div><div class="stat-info"><h3 id="rel-receita">R$ 0</h3><p>Receita Prevista</p></div></div>
                    <div class="stat-card"><div class="stat-icon" style="background:#ec4899;"><i class="fas fa-percentage"></i></div><div class="stat-info"><h3>0%</h3><p>Taxa de Conversão</p></div></div>
                </div>
            </div>
            <div id="page-whatsapp" class="page-content">
                <div class="card">
                    <div class="card-header"><h3><i class="fab fa-whatsapp"></i> Integração WhatsApp</h3></div>
                    <div class="card-body whatsapp-integration">
                        <div class="whatsapp-status"><i class="fas fa-check-circle" style="color:#10b981;"></i><span>WhatsApp Business conectado</span></div>
                        <div class="whatsapp-actions">
                            <button class="btn-primary"><i class="fab fa-whatsapp"></i> Enviar Mensagem em Massa</button>
                            <button class="btn-secondary"><i class="fas fa-robot"></i> Configurar Automação</button>
                        </div>
                    </div>
                </div>
            </div>
        </main>
    </div>
    <div id="modal-contato" class="modal">
        <div class="modal-content">
            <div class="modal-header"><h3 id="modal-contato-title">Novo Contato</h3><button class="modal-close" onclick="closeModal(\'modal-contato\')">&times;</button></div>
            <form id="form-contato" onsubmit="salvarContato(event)">
                <div class="form-row"><div class="form-group"><label>Nome *</label><input type="text" name="nome" required></div><div class="form-group"><label>Empresa</label><input type="text" name="empresa"></div></div>
                <div class="form-row"><div class="form-group"><label>Email</label><input type="email" name="email"></div><div class="form-group"><label>Telefone</label><input type="tel" name="telefone"></div></div>
                <div class="form-row"><div class="form-group"><label>WhatsApp</label><input type="tel" name="whatsapp"></div><div class="form-group"><label>Cargo</label><input type="text" name="cargo"></div></div>
                <div class="form-row"><div class="form-group"><label>Cidade</label><input type="text" name="cidade"></div><div class="form-group"><label>Estado</label><input type="text" name="estado"></div></div>
                <div class="form-row">
                    <div class="form-group"><label>Origem</label><select name="origem"><option value="">Selecione...</option><option value="Indicação">Indicação</option><option value="Site">Site</option><option value="Google Ads">Google Ads</option><option value="LinkedIn">LinkedIn</option><option value="Evento">Evento</option><option value="Outros">Outros</option></select></div>
                    <div class="form-group"><label>Status</label><select name="status"><option value="Lead">Lead</option><option value="Cliente">Cliente</option><option value="Prospect">Prospect</option></select></div>
                </div>
                <div class="form-group"><label>Tags</label><input type="text" name="tags" placeholder="Separe por vírgula"></div>
                <div class="form-group"><label>Notas</label><textarea name="notas" rows="3"></textarea></div>
                <div class="modal-footer"><button type="button" class="btn-secondary" onclick="closeModal(\'modal-contato\')">Cancelar</button><button type="submit" class="btn-primary">Salvar</button></div>
            </form>
        </div>
    </div>
    <div id="modal-oportunidade" class="modal">
        <div class="modal-content">
            <div class="modal-header"><h3 id="modal-oportunidade-title">Nova Oportunidade</h3><button class="modal-close" onclick="closeModal(\'modal-oportunidade\')">&times;</button></div>
            <form id="form-oportunidade" onsubmit="salvarOportunidade(event)">
                <div class="form-group"><label>Título *</label><input type="text" name="titulo" required></div>
                <div class="form-row"><div class="form-group"><label>Valor (R$)</label><input type="number" name="valor" step="0.01" value="0"></div><div class="form-group"><label>Etapa</label><select name="etapa"><option value="Prospecção">Prospecção</option><option value="Qualificação">Qualificação</option><option value="Proposta">Proposta</option><option value="Negociação">Negociação</option><option value="Fechamento">Fechamento</option></select></div></div>
                <div class="form-row"><div class="form-group"><label>Probabilidade (%)</label><input type="number" name="probabilidade" min="0" max="100" value="10"></div><div class="form-group"><label>Data Prevista</label><input type="date" name="data_prevista_fechamento"></div></div>
                <div class="form-group"><label>Descrição</label><textarea name="descricao" rows="3"></textarea></div>
                <div class="modal-footer"><button type="button" class="btn-secondary" onclick="closeModal(\'modal-oportunidade\')">Cancelar</button><button type="submit" class="btn-primary">Salvar</button></div>
            </form>
        </div>
    </div>
    <div id="modal-tarefa" class="modal">
        <div class="modal-content">
            <div class="modal-header"><h3 id="modal-tarefa-title">Nova Tarefa</h3><button class="modal-close" onclick="closeModal(\'modal-tarefa\')">&times;</button></div>
            <form id="form-tarefa" onsubmit="salvarTarefa(event)">
                <div class="form-group"><label>Título *</label><input type="text" name="titulo" required></div>
                <div class="form-row"><div class="form-group"><label>Tipo</label><select name="tipo"><option value="Ligação">Ligação</option><option value="Email">Email</option><option value="Reunião">Reunião</option><option value="WhatsApp">WhatsApp</option><option value="Outros">Outros</option></select></div><div class="form-group"><label>Prioridade</label><select name="prioridade"><option value="Baixa">Baixa</option><option value="Normal" selected>Normal</option><option value="Alta">Alta</option><option value="Urgente">Urgente</option></select></div></div>
                <div class="form-group"><label>Data/Hora</label><input type="datetime-local" name="data_hora"></div>
                <div class="form-group"><label>Descrição</label><textarea name="descricao" rows="3"></textarea></div>
                <div class="modal-footer"><button type="button" class="btn-secondary" onclick="closeModal(\'modal-tarefa\')">Cancelar</button><button type="submit" class="btn-primary">Salvar</button></div>
            </form>
        </div>
    </div>
    <script src="/static/js/app.js"></script>
</body>
</html>
''')

    write_file("frontend/static/css/style.css", '''\
*{margin:0;padding:0;box-sizing:border-box;font-family:\'Inter\',-apple-system,BlinkMacSystemFont,sans-serif}
body{background:#f3f4f6;color:#1f2937}
.app-container{display:flex;min-height:100vh}
.sidebar{width:260px;background:#1e293b;color:#fff;padding:24px 16px;position:fixed;height:100vh;overflow-y:auto}
.logo{display:flex;align-items:center;gap:12px;padding:8px 12px 24px;border-bottom:1px solid #334155;margin-bottom:24px}
.logo i{font-size:28px;color:#3b82f6}.logo h1{font-size:20px;font-weight:700}
.nav-menu{display:flex;flex-direction:column;gap:4px}
.nav-item{display:flex;align-items:center;gap:12px;padding:12px 16px;color:#cbd5e1;text-decoration:none;border-radius:8px;transition:all .2s;font-size:14px}
.nav-item:hover{background:#334155;color:#fff}.nav-item.active{background:#3b82f6;color:#fff}
.nav-item i{width:20px;text-align:center}
.main-content{flex:1;margin-left:260px}
.header{background:#fff;padding:16px 32px;display:flex;justify-content:space-between;align-items:center;box-shadow:0 1px 3px rgba(0,0,0,.1);position:sticky;top:0;z-index:10}
.header h2{font-size:22px}.header-right{display:flex;align-items:center;gap:16px}
.btn-icon{background:none;border:none;cursor:pointer;position:relative;padding:8px;border-radius:8px;color:#6b7280;transition:all .2s}
.btn-icon:hover{background:#f3f4f6;color:#1f2937}
.badge{position:absolute;top:2px;right:2px;background:#ef4444;color:#fff;font-size:10px;padding:2px 5px;border-radius:10px}
.user-menu{display:flex;align-items:center;gap:8px;color:#374151}.user-menu i{font-size:32px;color:#3b82f6}
.page-content{display:none;padding:24px 32px}.page-content.active{display:block}
.page-actions{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;gap:16px;flex-wrap:wrap}
.btn-primary{background:#3b82f6;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:500;display:inline-flex;align-items:center;gap:8px;transition:all .2s}
.btn-primary:hover{background:#2563eb}
.btn-secondary{background:#e5e7eb;color:#374151;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:500;transition:all .2s}
.btn-secondary:hover{background:#d1d5db}
.search-input{padding:10px 16px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;min-width:250px}
.search-input:focus{outline:none;border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.1)}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:20px;margin-bottom:24px}
.stat-card{background:#fff;border-radius:12px;padding:20px;display:flex;align-items:center;gap:16px;box-shadow:0 1px 3px rgba(0,0,0,.05);transition:transform .2s}
.stat-card:hover{transform:translateY(-2px)}
.stat-icon{width:56px;height:56px;border-radius:12px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:24px}
.stat-info h3{font-size:24px;font-weight:700}.stat-info p{color:#6b7280;font-size:14px}
.card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.05);margin-bottom:20px;overflow:hidden}
.card-header{padding:16px 20px;border-bottom:1px solid #e5e7eb}
.card-header h3{font-size:16px;display:flex;align-items:center;gap:8px}.card-header i{color:#3b82f6}
.card-body{padding:20px}.content-grid{display:grid;grid-template-columns:2fr 1fr;gap:20px}
.data-table{width:100%;border-collapse:collapse}
.data-table th{text-align:left;padding:12px 16px;background:#f9fafb;color:#6b7280;font-size:12px;font-weight:600;text-transform:uppercase;border-bottom:1px solid #e5e7eb}
.data-table td{padding:14px 16px;border-bottom:1px solid #f3f4f6;font-size:14px}
.data-table tr:hover{background:#f9fafb}
.action-btn{background:none;border:none;cursor:pointer;padding:6px 10px;border-radius:6px;color:#6b7280;transition:all .2s}
.action-btn:hover{background:#e5e7eb;color:#1f2937}
.action-btn.delete:hover{background:#fee2e2;color:#dc2626}
.action-btn.edit:hover{background:#dbeafe;color:#2563eb}
.kanban-board{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px;overflow-x:auto}
.kanban-column{background:#f3f4f6;border-radius:12px;padding:16px;min-height:400px;transition:background .2s}
.kanban-column.drag-over{background:#e0e7ff}
.kanban-column-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.kanban-column-header h4{font-size:14px}.kanban-count{background:#3b82f6;color:#fff;font-size:12px;padding:2px 8px;border-radius:10px}
.kanban-card{background:#fff;border-radius:8px;padding:14px;margin-bottom:10px;box-shadow:0 1px 2px rgba(0,0,0,.05);cursor:grab;transition:all .2s;user-select:none}
.kanban-card:hover{box-shadow:0 4px 12px rgba(0,0,0,.1);transform:translateY(-2px)}
.kanban-card.dragging{opacity:.5;cursor:grabbing}
.kanban-card-title{font-size:14px;font-weight:600;margin-bottom:6px}
.kanban-card-valor{font-size:16px;color:#10b981;font-weight:700;margin-bottom:8px}
.kanban-card-meta{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#6b7280}
.probability-bar{height:4px;background:#e5e7eb;border-radius:2px;overflow:hidden;margin-top:8px}
.probability-fill{height:100%;background:#3b82f6;border-radius:2px}
.funnel-stage{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid #f3f4f6}
.funnel-stage:last-child{border-bottom:none}
.funnel-label{flex:1;font-size:14px}.funnel-count{font-weight:600;min-width:40px}
.funnel-bar{flex:2;height:8px;background:#e5e7eb;border-radius:4px;overflow:hidden}
.funnel-bar-fill{height:100%;background:linear-gradient(90deg,#3b82f6,#10b981);border-radius:4px}
.task-item{display:flex;align-items:center;gap:12px;padding:12px 0;border-bottom:1px solid #f3f4f6}
.task-item:last-child{border-bottom:none}
.task-checkbox{width:20px;height:20px;cursor:pointer}.task-info{flex:1}
.task-title{font-size:14px;font-weight:500}.task-meta{font-size:12px;color:#6b7280;margin-top:2px}
.priority-badge{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600}
.priority-alta{background:#fee2e2;color:#dc2626}.priority-urgente{background:#dc2626;color:#fff}
.priority-normal{background:#dbeafe;color:#2563eb}.priority-baixa{background:#e5e7eb;color:#6b7280}
.calendar-header{display:flex;justify-content:space-between;align-items:center;padding:16px 20px}
.calendar-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;padding:16px}
.calendar-day-header{text-align:center;padding:8px;font-weight:600;color:#6b7280;font-size:12px}
.calendar-day{aspect-ratio:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding:6px;border-radius:8px;cursor:pointer;transition:all .2s;font-size:14px}
.calendar-day:hover{background:#f3f4f6}.calendar-day.today{background:#3b82f6;color:#fff}
.whatsapp-integration{text-align:center;padding:32px}
.whatsapp-status{display:inline-flex;align-items:center;gap:8px;font-size:16px;margin-bottom:24px;padding:10px 20px;background:#d1fae5;border-radius:20px}
.whatsapp-actions{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.5);z-index:1000;align-items:center;justify-content:center}
.modal.active{display:flex}
.modal-content{background:#fff;border-radius:12px;width:90%;max-width:600px;max-height:90vh;overflow-y:auto;animation:slideDown .3s ease}
@keyframes slideDown{from{opacity:0;transform:translateY(-20px)}to{opacity:1;transform:translateY(0)}}
.modal-header{display:flex;justify-content:space-between;align-items:center;padding:16px 20px;border-bottom:1px solid #e5e7eb}
.modal-header h3{font-size:18px}
.modal-close{background:none;border:none;font-size:28px;cursor:pointer;color:#9ca3af;line-height:1}
.modal-footer{display:flex;justify-content:flex-end;gap:12px;padding:16px 20px;border-top:1px solid #e5e7eb}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:16px}.form-group{padding:8px 20px}
.form-group label{display:block;font-size:13px;color:#374151;margin-bottom:6px;font-weight:500}
.form-group input,.form-group select,.form-group textarea{width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;font-family:inherit;transition:all .2s}
.form-group input:focus,.form-group select:focus,.form-group textarea:focus{outline:none;border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.1)}
.toast{position:fixed;bottom:24px;right:24px;background:#10b981;color:#fff;padding:12px 24px;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,.15);animation:slideIn .3s ease;z-index:2000}
@keyframes slideIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:translateX(0)}}
.empty-state{text-align:center;padding:40px 20px;color:#6b7280}
.empty-state i{font-size:48px;color:#d1d5db;margin-bottom:12px;display:block}
.status-badge{display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600}
.status-lead{background:#dbeafe;color:#1e40af}.status-cliente{background:#d1fae5;color:#065f46}.status-prospect{background:#fef3c7;color:#92400e}
@media(max-width:768px){.sidebar{width:70px}.logo h1,.nav-item span{display:none}.main-content{margin-left:70px}.form-row{grid-template-columns:1fr}.content-grid{grid-template-columns:1fr}}
''')

    write_file("frontend/static/js/app.js", '''\
const API_BASE = \'/api\';

let editandoContato = null;
let editandoOportunidade = null;
let editandoTarefa = null;
let editandoTarefaConcluida = false;

document.querySelectorAll(\'.nav-item\').forEach(item => {
    item.addEventListener(\'click\', e => {
        e.preventDefault();
        const page = item.getAttribute(\'data-page\');
        document.querySelectorAll(\'.nav-item\').forEach(i => i.classList.remove(\'active\'));
        item.classList.add(\'active\');
        document.querySelectorAll(\'.page-content\').forEach(p => p.classList.remove(\'active\'));
        document.getElementById(`page-${page}`).classList.add(\'active\');
        const titles = {
            dashboard: \'Dashboard\', contatos: \'Contatos\', oportunidades: \'Oportunidades\',
            tarefas: \'Tarefas\', calendario: \'Calendário\', relatorios: \'Relatórios\', whatsapp: \'WhatsApp\'
        };
        document.getElementById(\'page-title\').textContent = titles[page];
        loadPageData(page);
    });
});

function openModal(id) { document.getElementById(id).classList.add(\'active\'); }
function closeModal(id) {
    document.getElementById(id).classList.remove(\'active\');
    if (id === \'modal-contato\') {
        editandoContato = null;
        document.getElementById(\'modal-contato-title\').textContent = \'Novo Contato\';
        document.getElementById(\'form-contato\').reset();
    } else if (id === \'modal-oportunidade\') {
        editandoOportunidade = null;
        document.getElementById(\'modal-oportunidade-title\').textContent = \'Nova Oportunidade\';
        document.getElementById(\'form-oportunidade\').reset();
    } else if (id === \'modal-tarefa\') {
        editandoTarefa = null;
        editandoTarefaConcluida = false;
        document.getElementById(\'modal-tarefa-title\').textContent = \'Nova Tarefa\';
        document.getElementById(\'form-tarefa\').reset();
    }
}
window.onclick = e => { if (e.target.classList.contains(\'modal\')) closeModal(e.target.id); };

function abrirNovoContato() { closeModal(\'modal-contato\'); openModal(\'modal-contato\'); }
function abrirNovaOportunidade() { closeModal(\'modal-oportunidade\'); openModal(\'modal-oportunidade\'); }
function abrirNovaTarefa() { closeModal(\'modal-tarefa\'); openModal(\'modal-tarefa\'); }

async function apiCall(endpoint, options = {}) {
    try {
        const r = await fetch(`${API_BASE}${endpoint}`, { headers: { \'Content-Type\': \'application/json\' }, ...options });
        if (!r.ok) { const errorText = await r.text(); throw new Error(`Erro ${r.status}: ${errorText}`); }
        return await r.json();
    } catch (err) { console.error(\'[API Error]\', err); showToast(err.message, \'error\'); throw err; }
}

function showToast(msg, type = \'success\') {
    const t = document.createElement(\'div\'); t.className = \'toast\';
    if (type === \'error\') t.style.background = \'#ef4444\';
    t.textContent = msg; document.body.appendChild(t);
    setTimeout(() => { t.style.animation = \'slideIn .3s ease reverse\'; setTimeout(() => t.remove(), 300); }, 3000);
}

function formatDateTimeLocal(dt) { return dt ? new Date(dt).toISOString().slice(0, 16) : \'\'; }
function formatDateLocal(dt) { return dt ? new Date(dt).toISOString().slice(0, 10) : \'\'; }
function cleanFormData(raw) {
    const d = {};
    for (const [k, v] of Object.entries(raw)) d[k] = v === \'\' ? null : v;
    return d;
}

async function salvarContato(e) {
    e.preventDefault();
    const data = cleanFormData(Object.fromEntries(new FormData(e.target)));
    try {
        if (editandoContato) {
            await apiCall(`/contatos/${editandoContato}`, { method: \'PUT\', body: JSON.stringify(data) });
            showToast(\'Contato atualizado!\');
        } else {
            await apiCall(\'/contatos\', { method: \'POST\', body: JSON.stringify(data) });
            showToast(\'Contato salvo com sucesso!\');
        }
        closeModal(\'modal-contato\'); carregarContatos(); carregarDashboard();
    } catch (err) { console.error(err); }
}

async function editarContato(id) {
    try {
        const c = await apiCall(`/contatos/${id}`);
        editandoContato = id;
        document.getElementById(\'modal-contato-title\').textContent = \'Editar Contato\';
        const form = document.getElementById(\'form-contato\'); form.reset();
        [\'nome\',\'empresa\',\'email\',\'telefone\',\'whatsapp\',\'cargo\',\'cidade\',\'estado\',\'origem\',\'status\',\'tags\',\'notas\'].forEach(f => {
            if (form.elements[f] !== undefined) form.elements[f].value = c[f] || \'\';
        });
        openModal(\'modal-contato\');
    } catch (err) { console.error(err); }
}

async function carregarContatos() {
    try {
        const contatos = await apiCall(\'/contatos\');
        const tbody = document.getElementById(\'tbody-contatos\');
        if (!contatos || contatos.length === 0) {
            tbody.innerHTML = \'<tr><td colspan="6" class="empty-state"><i class="fas fa-users"></i><p>Nenhum contato encontrado</p></td></tr>\'; return;
        }
        tbody.innerHTML = contatos.map(c => `
            <tr>
                <td><strong>${c.nome}</strong></td>
                <td>${c.empresa || \'-\'}</td><td>${c.email || \'-\'}</td><td>${c.telefone || \'-\'}</td>
                <td><span class="status-badge status-${(c.status || \'lead\').toLowerCase()}">${c.status || \'Lead\'}</span></td>
                <td>
                    <button class="action-btn edit" onclick="editarContato(${c.id})" title="Editar"><i class="fas fa-edit"></i></button>
                    <button class="action-btn delete" onclick="deletarContato(${c.id})" title="Excluir"><i class="fas fa-trash"></i></button>
                </td>
            </tr>`).join(\'\');
    } catch (err) { console.error(err); }
}

async function deletarContato(id) {
    if (!confirm(\'Deletar este contato?\')) return;
    try { await apiCall(`/contatos/${id}`, { method: \'DELETE\' }); showToast(\'Deletado!\'); carregarContatos(); carregarDashboard(); }
    catch (err) { console.error(err); }
}

async function salvarOportunidade(e) {
    e.preventDefault();
    const data = cleanFormData(Object.fromEntries(new FormData(e.target)));
    data.valor = parseFloat(data.valor) || 0; data.probabilidade = parseInt(data.probabilidade) || 10;
    try {
        if (editandoOportunidade) {
            await apiCall(`/oportunidades/${editandoOportunidade}`, { method: \'PUT\', body: JSON.stringify(data) });
            showToast(\'Oportunidade atualizada!\');
        } else {
            await apiCall(\'/oportunidades\', { method: \'POST\', body: JSON.stringify(data) });
            showToast(\'Oportunidade criada!\');
        }
        closeModal(\'modal-oportunidade\'); carregarOportunidades(); carregarDashboard();
    } catch (err) { console.error(err); }
}

async function editarOportunidade(id) {
    try {
        const o = await apiCall(`/oportunidades/${id}`);
        editandoOportunidade = id;
        document.getElementById(\'modal-oportunidade-title\').textContent = \'Editar Oportunidade\';
        const form = document.getElementById(\'form-oportunidade\'); form.reset();
        form.elements[\'titulo\'].value = o.titulo || \'\';
        form.elements[\'valor\'].value = o.valor || 0;
        form.elements[\'etapa\'].value = o.etapa || \'Prospecção\';
        form.elements[\'probabilidade\'].value = o.probabilidade || 10;
        form.elements[\'data_prevista_fechamento\'].value = formatDateLocal(o.data_prevista_fechamento);
        form.elements[\'descricao\'].value = o.descricao || \'\';
        openModal(\'modal-oportunidade\');
    } catch (err) { console.error(err); }
}

async function carregarOportunidades() {
    try {
        const ops = await apiCall(\'/oportunidades\');
        const kanban = document.getElementById(\'kanban-oportunidades\');
        const etapas = [\'Prospecção\', \'Qualificação\', \'Proposta\', \'Negociação\', \'Fechamento\'];
        kanban.innerHTML = etapas.map(etapa => {
            const eOps = ops.filter(o => o.etapa === etapa);
            return `<div class="kanban-column" data-etapa="${etapa}">
                <div class="kanban-column-header"><h4>${etapa}</h4><span class="kanban-count">${eOps.length}</span></div>
                ${!eOps.length ? \'<div class="empty-state" style="padding:20px;"><i class="fas fa-inbox"></i><p>Vazio</p></div>\' :
                eOps.map(o => `<div class="kanban-card" draggable="true" data-id="${o.id}">
                    <div class="kanban-card-title">${o.titulo}</div>
                    <div class="kanban-card-valor">R$ ${o.valor.toLocaleString(\'pt-BR\')}</div>
                    <div class="kanban-card-meta">
                        <span>Prob: ${o.probabilidade}%</span>
                        <div>
                            <button class="action-btn edit" onclick="editarOportunidade(${o.id})" style="padding:2px 6px;" title="Editar"><i class="fas fa-edit"></i></button>
                            <button class="action-btn delete" onclick="deletarOportunidade(${o.id})" style="padding:2px 6px;"><i class="fas fa-trash"></i></button>
                        </div>
                    </div>
                    <div class="probability-bar"><div class="probability-fill" style="width:${o.probabilidade}%"></div></div>
                </div>`).join(\'\')}
            </div>`;
        }).join(\'\');
        setupDragAndDrop();
    } catch (err) { console.error(err); }
}

async function deletarOportunidade(id) {
    if (!confirm(\'Deletar esta oportunidade?\')) return;
    try { await apiCall(`/oportunidades/${id}`, { method: \'DELETE\' }); showToast(\'Deletada!\'); carregarOportunidades(); carregarDashboard(); }
    catch (err) { console.error(err); }
}

function setupDragAndDrop() {
    document.querySelectorAll(\'.kanban-card\').forEach(card => {
        card.addEventListener(\'dragstart\', e => {
            e.dataTransfer.setData(\'text/plain\', card.dataset.id);
            setTimeout(() => card.classList.add(\'dragging\'), 0);
        });
        card.addEventListener(\'dragend\', () => card.classList.remove(\'dragging\'));
    });
    document.querySelectorAll(\'.kanban-column\').forEach(col => {
        col.addEventListener(\'dragover\', e => { e.preventDefault(); col.classList.add(\'drag-over\'); });
        col.addEventListener(\'dragleave\', e => { if (!col.contains(e.relatedTarget)) col.classList.remove(\'drag-over\'); });
        col.addEventListener(\'drop\', async e => {
            e.preventDefault(); col.classList.remove(\'drag-over\');
            const id = e.dataTransfer.getData(\'text/plain\');
            if (!id) return;
            const novaEtapa = col.dataset.etapa;
            try {
                const op = await apiCall(`/oportunidades/${id}`);
                await apiCall(`/oportunidades/${id}`, { method: \'PUT\', body: JSON.stringify({ ...op, etapa: novaEtapa }) });
                carregarOportunidades(); carregarDashboard(); showToast(`Movido para ${novaEtapa}!`);
            } catch (err) { console.error(err); }
        });
    });
}

async function salvarTarefa(e) {
    e.preventDefault();
    const data = cleanFormData(Object.fromEntries(new FormData(e.target)));
    try {
        if (editandoTarefa) {
            data.concluida = editandoTarefaConcluida;
            await apiCall(`/tarefas/${editandoTarefa}`, { method: \'PUT\', body: JSON.stringify(data) });
            showToast(\'Tarefa atualizada!\');
        } else {
            await apiCall(\'/tarefas\', { method: \'POST\', body: JSON.stringify(data) });
            showToast(\'Tarefa criada!\');
        }
        closeModal(\'modal-tarefa\'); carregarTarefas(); carregarDashboard();
    } catch (err) { console.error(err); }
}

async function editarTarefa(id) {
    try {
        const t = await apiCall(`/tarefas/${id}`);
        editandoTarefa = id; editandoTarefaConcluida = t.concluida;
        document.getElementById(\'modal-tarefa-title\').textContent = \'Editar Tarefa\';
        const form = document.getElementById(\'form-tarefa\'); form.reset();
        form.elements[\'titulo\'].value = t.titulo || \'\';
        form.elements[\'tipo\'].value = t.tipo || \'Ligação\';
        form.elements[\'prioridade\'].value = t.prioridade || \'Normal\';
        form.elements[\'data_hora\'].value = formatDateTimeLocal(t.data_hora);
        form.elements[\'descricao\'].value = t.descricao || \'\';
        openModal(\'modal-tarefa\');
    } catch (err) { console.error(err); }
}

async function carregarTarefas() {
    try {
        const tarefas = await apiCall(\'/tarefas\');
        const tbody = document.getElementById(\'tbody-tarefas\');
        if (!tarefas || tarefas.length === 0) {
            tbody.innerHTML = \'<tr><td colspan="6" class="empty-state"><i class="fas fa-tasks"></i><p>Nenhuma tarefa</p></td></tr>\'; return;
        }
        tbody.innerHTML = tarefas.map(t => `
            <tr>
                <td><input type="checkbox" class="task-checkbox" ${t.concluida ? \'checked\' : \'\'} onchange="concluirTarefa(${t.id}, this.checked)"></td>
                <td style="${t.concluida ? \'text-decoration:line-through;color:#9ca3af;\' : \'\'}"><strong>${t.titulo}</strong></td>
                <td>${t.tipo || \'-\'}</td>
                <td>${t.data_hora ? new Date(t.data_hora).toLocaleString(\'pt-BR\') : \'-\'}</td>
                <td><span class="priority-badge priority-${(t.prioridade || \'normal\').toLowerCase()}">${t.prioridade || \'Normal\'}</span></td>
                <td>
                    <button class="action-btn edit" onclick="editarTarefa(${t.id})" title="Editar"><i class="fas fa-edit"></i></button>
                    <button class="action-btn delete" onclick="deletarTarefa(${t.id})"><i class="fas fa-trash"></i></button>
                </td>
            </tr>`).join(\'\');
    } catch (err) { console.error(err); }
}

async function concluirTarefa(id, ok) {
    if (!ok) return;
    try { await apiCall(`/tarefas/${id}/concluir`, { method: \'PUT\' }); showToast(\'Tarefa concluída!\'); carregarTarefas(); carregarDashboard(); }
    catch (err) { console.error(err); }
}

async function deletarTarefa(id) {
    if (!confirm(\'Deletar esta tarefa?\')) return;
    try { await apiCall(`/tarefas/${id}`, { method: \'DELETE\' }); showToast(\'Deletada!\'); carregarTarefas(); carregarDashboard(); }
    catch (err) { console.error(err); }
}

async function carregarDashboard() {
    try {
        const r = await apiCall(\'/relatorios/resumo\');
        document.getElementById(\'stat-contatos\').textContent = r.total_contatos;
        document.getElementById(\'stat-oportunidades\').textContent = r.total_oportunidades;
        document.getElementById(\'stat-tarefas\').textContent = r.tarefas_pendentes;
        document.getElementById(\'stat-valor\').textContent = `R$ ${r.valor_ponderado.toLocaleString(\'pt-BR\', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        document.getElementById(\'rel-receita\').textContent = document.getElementById(\'stat-valor\').textContent;
        const f = await apiCall(\'/relatorios/funil\');
        renderFunil(f.etapas); await renderTarefasPendentes();
    } catch (err) { console.error(err); }
}

function renderFunil(etapas) {
    const c = document.getElementById(\'funil-vendas\');
    if (!etapas || etapas.length === 0) { c.innerHTML = \'<div class="empty-state"><i class="fas fa-chart-pie"></i><p>Sem dados</p></div>\'; return; }
    const mx = Math.max(...etapas.map(e => e.quantidade));
    c.innerHTML = etapas.map(e => `
        <div class="funnel-stage">
            <div class="funnel-label">${e.etapa}</div><div class="funnel-count">${e.quantidade}</div>
            <div class="funnel-bar"><div class="funnel-bar-fill" style="width:${(e.quantidade / mx) * 100}%"></div></div>
        </div>`).join(\'\');
}

async function renderTarefasPendentes() {
    try {
        const ts = await apiCall(\'/tarefas\');
        const ps = ts.filter(t => !t.concluida).slice(0, 5);
        const c = document.getElementById(\'tarefas-pendentes\');
        if (!ps.length) { c.innerHTML = \'<div class="empty-state"><i class="fas fa-check-circle"></i><p>Tudo feito!</p></div>\'; return; }
        c.innerHTML = ps.map(t => `
            <div class="task-item">
                <input type="checkbox" class="task-checkbox" onchange="concluirTarefa(${t.id}, this.checked)">
                <div class="task-info"><div class="task-title">${t.titulo}</div><div class="task-meta">${t.tipo || \'\'} ${t.data_hora ? \'• \' + new Date(t.data_hora).toLocaleDateString(\'pt-BR\') : \'\'}</div></div>
                <span class="priority-badge priority-${(t.prioridade || \'normal\').toLowerCase()}">${t.prioridade || \'Normal\'}</span>
            </div>`).join(\'\');
    } catch (err) { console.error(err); }
}

function renderizarCalendario() {
    const grid = document.getElementById(\'calendar-grid\'); grid.innerHTML = \'\';
    const today = new Date(), ano = today.getFullYear(), mes = today.getMonth();
    const primeiroDia = new Date(ano, mes, 1).getDay(), ultimoDia = new Date(ano, mes + 1, 0).getDate();
    document.getElementById(\'cal-title\').textContent = today.toLocaleDateString(\'pt-BR\', { month: \'long\', year: \'numeric\' }).replace(/^\\w/, c => c.toUpperCase());
    [\'Dom\',\'Seg\',\'Ter\',\'Qua\',\'Qui\',\'Sex\',\'Sáb\'].forEach(d => {
        const el = document.createElement(\'div\'); el.className = \'calendar-day-header\'; el.textContent = d; grid.appendChild(el);
    });
    for (let i = 0; i < primeiroDia; i++) { const el = document.createElement(\'div\'); el.className = \'calendar-day\'; grid.appendChild(el); }
    for (let d = 1; d <= ultimoDia; d++) {
        const el = document.createElement(\'div\'); el.className = \'calendar-day\'; el.textContent = d;
        if (d === today.getDate()) el.classList.add(\'today\'); grid.appendChild(el);
    }
}

document.getElementById(\'search-contatos\')?.addEventListener(\'input\', e => {
    const termo = e.target.value.toLowerCase();
    document.querySelectorAll(\'#tbody-contatos tr\').forEach(r => { r.style.display = r.textContent.toLowerCase().includes(termo) ? \'\' : \'none\'; });
});

function loadPageData(page) {
    switch (page) {
        case \'dashboard\': carregarDashboard(); break;
        case \'contatos\': carregarContatos(); break;
        case \'oportunidades\': carregarOportunidades(); break;
        case \'tarefas\': carregarTarefas(); break;
        case \'calendario\': renderizarCalendario(); break;
        case \'relatorios\': carregarDashboard(); break;
    }
}

document.addEventListener(\'DOMContentLoaded\', () => { carregarDashboard(); });
''')

    write_file("README.md",
        "# CRM Brasileiro\n"
        "Sistema de CRM completo em Python.\n\n"
        "## Como Rodar\n"
        "Basta dar dois cliques no arquivo `iniciar_crm.bat`\n"
    )

    write_file("iniciar_crm.bat",
        "@echo off\n"
        "chcp 65001 >nul\n"
        "echo ==========================================\n"
        "echo   Iniciando CRM Brasileiro\n"
        "echo ==========================================\n"
        "echo.\n"
        "if not exist \"venv\\Scripts\\python.exe\" (\n"
        "    echo [ERRO] Ambiente virtual nao encontrado.\n"
        "    echo Execute 'python criar_crm.py' primeiro.\n"
        "    pause\n"
        "    exit /b\n"
        ")\n"
        "echo [1/3] Verificando dependencias...\n"
        "venv\\Scripts\\python.exe -c \"import fastapi\" >nul 2>&1\n"
        "if errorlevel 1 (\n"
        "    echo [!] Instalando dependencias...\n"
        "    venv\\Scripts\\pip.exe install -r requirements.txt\n"
        ")\n"
        "echo [2/3] Ambiente pronto.\n"
        "echo [3/3] Iniciando servidor...\n"
        "echo  Acesse: http://localhost:8000\n"
        "echo.\n"
        "venv\\Scripts\\python.exe backend\\main.py\n"
        "pause\n"
    )

    print("\n🐍 Criando ambiente virtual...")
    try:
        subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
        print("  ✓ venv/ criado")
        pip = os.path.join("venv", "Scripts" if os.name == "nt" else "bin", "pip")
        print("\n📥 Instalando dependências...")
        subprocess.run([pip, "install", "--upgrade", "pip"], check=True, capture_output=True)
        subprocess.run([pip, "install", "-r", "requirements.txt"], check=True)
        print("  ✓ Dependências instaladas!")
    except Exception as e:
        print(f"  ⚠️ Erro: {e}")
        print("  💡 Crie manualmente: python -m venv venv && pip install -r requirements.txt")

    print("\n" + "=" * 50)
    print("🎉 PROJETO CRIADO COM SUCESSO!")
    print("=" * 50)
    print("\n📋 Para iniciar, dê dois cliques em: iniciar_crm.bat\n")

if __name__ == "__main__":
    criar_projeto()
