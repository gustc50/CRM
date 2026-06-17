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
    for k, v in data.model_dump().items():
        setattr(c, k, v)
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
    for k, v in data.model_dump().items():
        setattr(t, k, v)
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
    print("\n🚀 CRM Brasileiro iniciando...")
    print("📡 Acesse: http://localhost:8000\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
