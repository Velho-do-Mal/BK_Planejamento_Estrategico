# streamlit_app.py
"""
BK_Planejamento_Estrategico v2.0 — Layout e UX aprimorados
- Dashboard executivo com KPIs
- Tabelas 100% editáveis (estilo Excel) com st.data_editor
- Gráficos modernos com Plotly (dark theme + cores BK)
- SWOT visual 4-quadrantes
- OKRs: previsto vs realizado, tendência, gauge de performance
- Planos de Ação: kanban-style analytics + timeline
- Relatório HTML moderno + exportação .docx integrada
- Correções: build_example, StrategicInfo seguro, conn_str mascarada, typos
"""

import base64
import io
import json
import os
import sqlite3
import zipfile
import inspect
from dataclasses import dataclass, asdict, field, fields
from datetime import date, datetime
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd
import plotly.graph_objs as go
import plotly.express as px
from plotly.subplots import make_subplots
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

# ============================================
# CONFIGURAÇÃO DO BANCO DE DADOS (Neon)
# ============================================
_DB_CONN_FALLBACK = "postgresql://neondb_owner:npg_TiJv0WHSG7pU@ep-jolly-heart-ahj739cl-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require"

def _sanitize_conn_str(conn_str: str) -> str:
    """
    Remove parâmetros incompatíveis com pgBouncer (pooler Neon).
    channel_binding=require NÃO é suportado pelo pgBouncer e causa falha silenciosa
    de autenticação SCRAM-SHA-256. Deve ser removido da URL antes de criar o engine.
    """
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(conn_str)
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        # Parâmetros que o pgBouncer não suporta
        for incompatible in ("channel_binding", "options"):
            params.pop(incompatible, None)
        new_query = urllib.parse.urlencode(
            {k: v[0] for k, v in params.items()}, safe="")
        fixed = parsed._replace(query=new_query)
        return urllib.parse.urlunparse(fixed)
    except Exception:
        return conn_str

def _get_db_conn_str() -> str:
    """Retorna connection string sanitizada: secrets.toml > env var > hardcoded fallback."""
    try:
        secret = st.secrets.get("neon", {}).get("connection", "")
        if secret and secret.strip():
            return _sanitize_conn_str(secret.strip())
    except Exception:
        pass
    env = os.environ.get("NEON_DATABASE_URL", "")
    if env:
        return _sanitize_conn_str(env)
    return _DB_CONN_FALLBACK

# Será resolvido após st estar disponível
DB_CONN_STR = _DB_CONN_FALLBACK  # placeholder — atualizado no boot

# ============================================
# PALETA BK — cores consistentes
# ============================================
BK_BLUE       = "#3B82F6"
BK_BLUE_LIGHT = "#93C5FD"
BK_BLUE_DARK  = "#1E40AF"
BK_TEAL       = "#14B8A6"
BK_GREEN      = "#10B981"
BK_ORANGE     = "#F59E0B"
BK_RED        = "#EF4444"
BK_PURPLE     = "#8B5CF6"
BK_GRAY       = "#64748B"
BK_BG         = "#0F172A"
BK_SURFACE    = "#1E293B"
BK_BORDER     = "#334155"
BK_TEXT       = "#F1F5F9"
BK_TEXT_MUTED = "#94A3B8"
BK_CARD       = "#1E293B"
BK_DARK       = "#0F172A"

SWOT_COLORS = {
    "Força":       "#10B981",
    "Fraqueza":    "#EF4444",
    "Oportunidade":"#3B82F6",
    "Ameaça":      "#F59E0B",
}
STATUS_COLORS = {
    "Concluído":   "#10B981",
    "Em andamento":"#F59E0B",
    "Pendente":    "#64748B",
    "Atrasado":    "#EF4444",
}

# ============================================
# MODELOS DE DADOS
# ============================================

@dataclass
class Partner:
    nome: str
    cargo: str
    email: str
    telefone: str
    observacoes: str = ""

@dataclass
class AreaResponsavel:
    area: str
    responsavel: str
    email: str
    observacoes: str = ""

@dataclass
class StrategicInfo:
    visao: str = ""
    missao: str = ""
    valores: str = ""
    proposta_valor: str = ""
    posicionamento: str = ""
    objetivos_estrategicos: str = ""
    pilares: str = ""
    publico_alvo: str = ""
    diferenciais: str = ""
    notas: str = ""

@dataclass
class SWOTItem:
    tipo: str
    descricao: str
    prioridade: str

@dataclass
class OKRMonthData:
    ano: int
    mes: int
    previsto: float = 0.0
    realizado: float = 0.0

@dataclass
class OKR:
    nome: str
    area: str
    unidade: str
    descricao: str = ""
    inicio_ano: int = field(default_factory=lambda: date.today().year)
    inicio_mes: int = field(default_factory=lambda: date.today().month)
    meses: List[OKRMonthData] = field(default_factory=list)

    def __post_init__(self):
        if not self.meses:
            ano, mes = self.inicio_ano, self.inicio_mes
            for _ in range(36):
                self.meses.append(OKRMonthData(ano=ano, mes=mes))
                mes += 1
                if mes > 12:
                    mes = 1
                    ano += 1

@dataclass
class PlanoAcao:
    titulo: str
    area: str
    responsavel: str
    descricao: str
    data_inicio: str
    data_vencimento: str
    status: str = "Pendente"
    observacoes: str = ""
    okr: str = ""
    como_fazer: str = ""

@dataclass
class PlanningData:
    strategic: StrategicInfo = field(default_factory=StrategicInfo)
    partners: List[Partner] = field(default_factory=list)
    areas: List[AreaResponsavel] = field(default_factory=list)
    swot: List[SWOTItem] = field(default_factory=list)
    okrs: List[OKR] = field(default_factory=list)
    actions: List[PlanoAcao] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategic": asdict(self.strategic),
            "partners": [asdict(p) for p in self.partners],
            "areas": [asdict(a) for a in self.areas],
            "swot": [asdict(s) for s in self.swot],
            "okrs": [
                {**{k: v for k, v in asdict(o).items() if k != "meses"},
                 "meses": [asdict(m) for m in o.meses]}
                for o in self.okrs
            ],
            "actions": [asdict(ac) for ac in self.actions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanningData":
        pd_obj = cls()
        # StrategicInfo seguro contra chaves extras
        try:
            known = {f.name for f in fields(StrategicInfo)}
            safe = {k: v for k, v in data.get("strategic", {}).items() if k in known}
            pd_obj.strategic = StrategicInfo(**safe)
        except Exception:
            pd_obj.strategic = StrategicInfo()

        for p in data.get("partners", []):
            try: pd_obj.partners.append(Partner(**p))
            except Exception: pass

        for a in data.get("areas", []):
            try: pd_obj.areas.append(AreaResponsavel(**a))
            except Exception: pass

        for s in data.get("swot", []):
            try: pd_obj.swot.append(SWOTItem(**s))
            except Exception: pass

        for o in data.get("okrs", []):
            try:
                meses = [OKRMonthData(**m) for m in o.get("meses", [])]
                o_copy = {k: v for k, v in o.items() if k != "meses"}
                okr = OKR(**o_copy)
                if meses:
                    okr.meses = meses
                if len(okr.meses) != 36:
                    existing = {(int(m.ano), int(m.mes)): m for m in okr.meses}
                    okr.meses = []
                    ano, mes = int(okr.inicio_ano), int(okr.inicio_mes)
                    for _ in range(36):
                        key = (ano, mes)
                        if key in existing:
                            mm = existing[key]
                            okr.meses.append(OKRMonthData(ano=ano, mes=mes, previsto=float(mm.previsto), realizado=float(mm.realizado)))
                        else:
                            okr.meses.append(OKRMonthData(ano=ano, mes=mes))
                        mes += 1
                        if mes > 12: mes, ano = 1, ano + 1
                pd_obj.okrs.append(okr)
            except Exception:
                pass

        for ac in data.get("actions", []):
            try:
                if "data_inicio" not in ac:
                    ac = {**ac, "data_inicio": ac.get("data_vencimento", date.today().strftime("%Y-%m-%d"))}
                # Garante campos novos (retrocompatibilidade)
                ac.setdefault("okr", "")
                ac.setdefault("como_fazer", "")
                pd_obj.actions.append(PlanoAcao(**ac))
            except Exception:
                try:
                    pd_obj.actions.append(PlanoAcao(
                        titulo=ac.get("titulo",""), area=ac.get("area",""),
                        responsavel=ac.get("responsavel",""), descricao=ac.get("descricao",""),
                        data_inicio=ac.get("data_inicio", date.today().strftime("%Y-%m-%d")),
                        data_vencimento=ac.get("data_vencimento", date.today().strftime("%Y-%m-%d")),
                        status=ac.get("status","Pendente"), observacoes=ac.get("observacoes",""),
                        okr=ac.get("okr",""), como_fazer=ac.get("como_fazer","")
                    ))
                except Exception:
                    pass
        return pd_obj


def build_example() -> "PlanningData":
    """Dados de exemplo para demonstração."""
    return PlanningData()


# ============================================
# UTILITÁRIOS DE DADOS / EXPORT
# ============================================

def okr_to_dataframe(okr: OKR) -> pd.DataFrame:
    rows = []
    for i, m in enumerate(okr.meses, start=1):
        rows.append({"m_index": i, "ano": m.ano, "mes": m.mes,
                     "previsto": m.previsto, "realizado": m.realizado})
    return pd.DataFrame(rows)

def planning_to_dataframes(planning: PlanningData) -> Dict[str, pd.DataFrame]:
    df_partners = pd.DataFrame([asdict(p) for p in planning.partners]) if planning.partners else pd.DataFrame(columns=["nome","cargo","email","telefone","observacoes"])
    df_areas = pd.DataFrame([asdict(a) for a in planning.areas]) if planning.areas else pd.DataFrame(columns=["area","responsavel","email","observacoes"])
    df_swot = pd.DataFrame([asdict(s) for s in planning.swot]) if planning.swot else pd.DataFrame(columns=["tipo","descricao","prioridade"])
    okr_rows, okrmes_rows = [], []
    for idx, o in enumerate(planning.okrs, start=1):
        okr_rows.append({"okr_id": idx, "nome": o.nome, "area": o.area, "unidade": o.unidade,
                         "descricao": o.descricao, "inicio_ano": o.inicio_ano, "inicio_mes": o.inicio_mes})
        for i, m in enumerate(o.meses, start=1):
            okrmes_rows.append({"okr_id": idx, "idx_mes": i, "ano": m.ano, "mes": m.mes,
                                 "previsto": m.previsto, "realizado": m.realizado})
    df_okr = pd.DataFrame(okr_rows) if okr_rows else pd.DataFrame(columns=["okr_id","nome","area","unidade","descricao","inicio_ano","inicio_mes"])
    df_okr_mes = pd.DataFrame(okrmes_rows) if okrmes_rows else pd.DataFrame(columns=["okr_id","idx_mes","ano","mes","previsto","realizado"])
    df_actions = pd.DataFrame([asdict(a) for a in planning.actions]) if planning.actions else pd.DataFrame(columns=["titulo","area","responsavel","descricao","data_inicio","data_vencimento","status","observacoes","okr","como_fazer"])
    return {"partners": df_partners, "areas": df_areas, "swot": df_swot,
            "okrs": df_okr, "okr_mes": df_okr_mes, "actions": df_actions}

def export_to_csv_zip(planning: PlanningData) -> bytes:
    dfs = planning_to_dataframes(planning)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, df in dfs.items():
            df_out = df.copy()
            for c in df_out.columns:
                if pd.api.types.is_datetime64_any_dtype(df_out[c]):
                    df_out[c] = df_out[c].dt.strftime("%Y-%m-%d")
            zf.writestr(f"{name}.csv", df_out.to_csv(index=False).encode("utf-8"))
    buf.seek(0)
    return buf.read()

def export_to_excel_bytes(planning: PlanningData) -> bytes:
    dfs = planning_to_dataframes(planning)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in dfs.items():
            try:
                df_out = df.copy()
                for c in df_out.columns:
                    if pd.api.types.is_datetime64_any_dtype(df_out[c]):
                        df_out[c] = df_out[c].dt.strftime("%Y-%m-%d")
                df_out.to_excel(writer, sheet_name=name[:31], index=False)
            except Exception:
                pd.DataFrame().to_excel(writer, sheet_name=name[:31], index=False)
    buf.seek(0)
    return buf.read()

def _make_pg_engine(conn_str: str):
    """Cria engine SQLAlchemy robusto para Neon/pgBouncer + Streamlit Cloud.
    NullPool: obrigatório no Streamlit — cada rerun cria nova conexão, pool persistente
              causa "connection already closed" e vazamentos no Neon free tier.
    connect_timeout: evita travamento quando Neon acorda do sleep (free tier ~5s).
    """
    conn_str = _sanitize_conn_str(conn_str)
    return create_engine(
        conn_str,
        poolclass=NullPool,
        connect_args={"connect_timeout": 20},
    )


def export_to_postgres(planning: PlanningData, conn_str: str = "") -> str:
    if not conn_str:
        conn_str = _get_db_conn_str()
    if not conn_str:
        return "❌ Connection string vazia. Configure NEON_DATABASE_URL ou st.secrets['neon']['connection']."
    try:
        engine = _make_pg_engine(conn_str)
    except Exception as e:
        return f"❌ Erro ao criar engine: {e}"

    ddl_statements = [
        """CREATE TABLE IF NOT EXISTS strategic (
            id INTEGER PRIMARY KEY DEFAULT 1,
            visao TEXT DEFAULT '', missao TEXT DEFAULT '', valores TEXT DEFAULT '',
            proposta_valor TEXT DEFAULT '', posicionamento TEXT DEFAULT '',
            objetivos_estrategicos TEXT DEFAULT '', pilares TEXT DEFAULT '',
            publico_alvo TEXT DEFAULT '', diferenciais TEXT DEFAULT '', notas TEXT DEFAULT '',
            CHECK (id = 1));""",
        """CREATE TABLE IF NOT EXISTS partners (id SERIAL PRIMARY KEY, nome TEXT NOT NULL,
           cargo TEXT, email TEXT, telefone TEXT, observacoes TEXT, UNIQUE (nome, email));""",
        """CREATE TABLE IF NOT EXISTS areas (id SERIAL PRIMARY KEY, area TEXT NOT NULL,
           responsavel TEXT, email TEXT, observacoes TEXT, UNIQUE (area));""",
        """CREATE TABLE IF NOT EXISTS swot (id SERIAL PRIMARY KEY, tipo TEXT NOT NULL,
           descricao TEXT NOT NULL, prioridade TEXT NOT NULL, UNIQUE (tipo, descricao));""",
        """CREATE TABLE IF NOT EXISTS okr (id SERIAL PRIMARY KEY, nome TEXT NOT NULL,
           area TEXT, unidade TEXT, descricao TEXT, inicio_ano INTEGER, inicio_mes INTEGER, UNIQUE (nome));""",
        """CREATE TABLE IF NOT EXISTS okr_mes (id SERIAL PRIMARY KEY,
           okr_id INTEGER NOT NULL REFERENCES okr(id) ON DELETE CASCADE,
           idx_mes INTEGER NOT NULL, ano INTEGER NOT NULL, mes INTEGER NOT NULL,
           previsto DOUBLE PRECISION NOT NULL, realizado DOUBLE PRECISION NOT NULL, UNIQUE (okr_id, idx_mes));""",
        """CREATE TABLE IF NOT EXISTS actions (id SERIAL PRIMARY KEY, titulo TEXT NOT NULL,
           area TEXT, responsavel TEXT, descricao TEXT, data_inicio DATE, data_vencimento DATE,
           status TEXT NOT NULL, observacoes TEXT, okr TEXT DEFAULT '', como_fazer TEXT DEFAULT '',
           UNIQUE (titulo, data_vencimento));""",
    ]
    try:
        with engine.begin() as conn:
            for ddl in ddl_statements:
                conn.execute(text(ddl))

            # Garantir que colunas novas existam em tabelas antigas (migrações cumulativas)
            for _col, _def in [
                ("okr",         "TEXT DEFAULT ''"),
                ("como_fazer",  "TEXT DEFAULT ''"),
                ("data_inicio", "DATE"),   # adicionada em v2 — pode não existir em BDs criados antes
            ]:
                try:
                    conn.execute(text(f"ALTER TABLE actions ADD COLUMN IF NOT EXISTS {_col} {_def}"))
                except Exception:
                    pass

            # ── Strategic (singleton row) ──
            s = planning.strategic
            conn.execute(text("""
                INSERT INTO strategic (id,visao,missao,valores,proposta_valor,posicionamento,
                    objetivos_estrategicos,pilares,publico_alvo,diferenciais,notas)
                VALUES (1,:visao,:missao,:valores,:proposta_valor,:posicionamento,
                    :objetivos_estrategicos,:pilares,:publico_alvo,:diferenciais,:notas)
                ON CONFLICT (id) DO UPDATE SET
                    visao=EXCLUDED.visao, missao=EXCLUDED.missao, valores=EXCLUDED.valores,
                    proposta_valor=EXCLUDED.proposta_valor, posicionamento=EXCLUDED.posicionamento,
                    objetivos_estrategicos=EXCLUDED.objetivos_estrategicos, pilares=EXCLUDED.pilares,
                    publico_alvo=EXCLUDED.publico_alvo, diferenciais=EXCLUDED.diferenciais,
                    notas=EXCLUDED.notas"""),
                {"visao":s.visao,"missao":s.missao,"valores":s.valores,
                 "proposta_valor":s.proposta_valor,"posicionamento":s.posicionamento,
                 "objetivos_estrategicos":s.objetivos_estrategicos,"pilares":s.pilares,
                 "publico_alvo":s.publico_alvo,"diferenciais":s.diferenciais,"notas":s.notas})

            # ── Partners (delete+reinsert para sincronizar exclusões) ──
            conn.execute(text("DELETE FROM partners"))
            for p in planning.partners:
                conn.execute(text("""INSERT INTO partners (nome,cargo,email,telefone,observacoes)
                    VALUES (:nome,:cargo,:email,:telefone,:observacoes)"""),
                    {"nome":p.nome,"cargo":p.cargo,"email":p.email,"telefone":p.telefone,"observacoes":p.observacoes})

            # ── Areas ──
            conn.execute(text("DELETE FROM areas"))
            for a in planning.areas:
                conn.execute(text("""INSERT INTO areas (area,responsavel,email,observacoes)
                    VALUES (:area,:responsavel,:email,:observacoes)"""),
                    {"area":a.area,"responsavel":a.responsavel,"email":a.email,"observacoes":a.observacoes})

            # ── SWOT ──
            conn.execute(text("DELETE FROM swot"))
            for sw in planning.swot:
                conn.execute(text("""INSERT INTO swot (tipo,descricao,prioridade)
                    VALUES (:tipo,:descricao,:prioridade)"""),
                    {"tipo":sw.tipo,"descricao":sw.descricao,"prioridade":sw.prioridade})

            # ── OKRs (delete cascade limpa okr_mes junto) ──
            conn.execute(text("DELETE FROM okr_mes"))
            conn.execute(text("DELETE FROM okr"))
            for o in planning.okrs:
                res = conn.execute(text("""INSERT INTO okr (nome,area,unidade,descricao,inicio_ano,inicio_mes)
                    VALUES (:nome,:area,:unidade,:descricao,:inicio_ano,:inicio_mes)
                    RETURNING id"""),
                    {"nome":o.nome,"area":o.area,"unidade":o.unidade,"descricao":o.descricao,
                     "inicio_ano":o.inicio_ano,"inicio_mes":o.inicio_mes})
                row = res.fetchone()
                okr_id = row[0] if row else None
                if okr_id:
                    for idx, m in enumerate(o.meses, start=1):
                        conn.execute(text("""INSERT INTO okr_mes (okr_id,idx_mes,ano,mes,previsto,realizado)
                            VALUES (:okr_id,:idx_mes,:ano,:mes,:previsto,:realizado)"""),
                            {"okr_id":okr_id,"idx_mes":idx,"ano":m.ano,"mes":m.mes,
                             "previsto":m.previsto,"realizado":m.realizado})

            # ── Actions ──
            conn.execute(text("DELETE FROM actions"))
            for ac in planning.actions:
                try:
                    datetime.strptime(ac.data_vencimento, "%Y-%m-%d")
                    dv = ac.data_vencimento
                except Exception:
                    dv = None
                if dv is None:
                    continue
                di = getattr(ac, "data_inicio", None)
                if di:
                    try:
                        datetime.strptime(di, "%Y-%m-%d")
                    except Exception:
                        di = dv
                conn.execute(text("""INSERT INTO actions (titulo,area,responsavel,descricao,data_inicio,data_vencimento,status,observacoes,okr,como_fazer)
                    VALUES (:titulo,:area,:responsavel,:descricao,:data_inicio,:data_vencimento,:status,:observacoes,:okr,:como_fazer)"""),
                    {"titulo":ac.titulo,"area":ac.area,"responsavel":ac.responsavel,"descricao":ac.descricao,
                     "data_inicio":di,"data_vencimento":dv,
                     "status":ac.status,"observacoes":ac.observacoes,
                     "okr":getattr(ac,"okr",""),"como_fazer":getattr(ac,"como_fazer","")})

        return "✅ Exportação para PostgreSQL (Neon) concluída com sucesso."
    except Exception as e:
        return f"❌ Erro durante exportação para Postgres: {e}"


# ============================================
# FUNÇÕES DE CARREGAMENTO DO BANCO
# ============================================

def load_from_postgres(conn_str: str) -> Optional[PlanningData]:
    """Carrega dados do PostgreSQL e retorna um objeto PlanningData."""
    try:
        engine = _make_pg_engine(conn_str)
        with engine.connect() as conn:
            # Verifica se as tabelas existem
            check = conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename='okr'"))
            if check.fetchone() is None:
                return None  # BD vazio — sem tabelas criadas ainda

            # ── Strategic ──
            strategic = StrategicInfo()
            try:
                df_strat = pd.read_sql("SELECT * FROM strategic WHERE id=1", conn)
                if not df_strat.empty:
                    row = df_strat.iloc[0].to_dict()
                    known = {f.name for f in fields(StrategicInfo)}
                    safe = {k: (v if v is not None else "") for k, v in row.items() if k in known}
                    strategic = StrategicInfo(**safe)
            except Exception:
                pass  # tabela pode não existir ainda

            # ── Dados tabulares ──
            df_partners = pd.read_sql("SELECT nome,cargo,email,telefone,observacoes FROM partners", conn)
            df_areas = pd.read_sql("SELECT area,responsavel,email,observacoes FROM areas", conn)
            df_swot = pd.read_sql("SELECT tipo,descricao,prioridade FROM swot", conn)
            df_actions = pd.read_sql("SELECT titulo,area,responsavel,descricao,data_inicio,data_vencimento,status,observacoes,COALESCE(okr,'') as okr,COALESCE(como_fazer,'') as como_fazer FROM actions", conn)
            df_okr = pd.read_sql("SELECT id,nome,area,unidade,descricao,inicio_ano,inicio_mes FROM okr", conn)
            df_okr_mes = pd.read_sql("SELECT okr_id,idx_mes,ano,mes,previsto,realizado FROM okr_mes ORDER BY okr_id, idx_mes", conn)

        # Constrói objetos
        partners = [Partner(**row) for _, row in df_partners.iterrows()]
        areas = [AreaResponsavel(**row) for _, row in df_areas.iterrows()]
        swot = [SWOTItem(**row) for _, row in df_swot.iterrows()]

        actions = []
        for _, row in df_actions.iterrows():
            act = row.to_dict()
            for dcol in ("data_inicio", "data_vencimento"):
                v = act.get(dcol)
                if v is not None and not isinstance(v, str):
                    try:
                        act[dcol] = v.strftime("%Y-%m-%d")
                    except Exception:
                        act[dcol] = str(v)
                elif v is None:
                    act[dcol] = date.today().strftime("%Y-%m-%d")
            actions.append(PlanoAcao(**act))

        okrs = []
        for _, okr_row in df_okr.iterrows():
            okr_id = okr_row["id"]
            meses_df = df_okr_mes[df_okr_mes["okr_id"] == okr_id].sort_values("idx_mes")
            meses = []
            for _, mrow in meses_df.iterrows():
                meses.append(OKRMonthData(
                    ano=int(mrow["ano"]),
                    mes=int(mrow["mes"]),
                    previsto=float(mrow["previsto"]),
                    realizado=float(mrow["realizado"])
                ))
            okr = OKR(
                nome=okr_row["nome"],
                area=okr_row["area"] or "",
                unidade=okr_row["unidade"] or "",
                descricao=okr_row["descricao"] or "",
                inicio_ano=int(okr_row["inicio_ano"]),
                inicio_mes=int(okr_row["inicio_mes"]),
                meses=meses
            )
            okrs.append(okr)

        planning = PlanningData(
            strategic=strategic,
            partners=partners,
            areas=areas,
            swot=swot,
            okrs=okrs,
            actions=actions
        )
        return planning
    except Exception as e:
        print(f"Erro ao carregar do PostgreSQL: {e}")
        return None


# ============================================
# PERSISTÊNCIA LOCAL — SQLite
# ============================================

SQLITE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bk_planejamento.db")

def _sqlite_conn():
    return sqlite3.connect(SQLITE_PATH)

def save_to_sqlite(planning: PlanningData) -> str:
    """Salva todos os dados no SQLite local (bk_planejamento.db)."""
    try:
        conn = _sqlite_conn()
        c = conn.cursor()
        # DDL
        c.execute("""CREATE TABLE IF NOT EXISTS planning_json (
            id INTEGER PRIMARY KEY CHECK (id=1),
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        # Salva como JSON completo (simples, robusto, sem perda de schema)
        data_json = json.dumps(planning.to_dict(), ensure_ascii=False)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("""INSERT INTO planning_json (id, data, updated_at) VALUES (1, ?, ?)
                     ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at""",
                  (data_json, now))
        conn.commit()
        conn.close()
        return "✅"
    except Exception as e:
        return f"❌ SQLite: {e}"

def load_from_sqlite() -> Optional[PlanningData]:
    """Carrega dados do SQLite local."""
    if not os.path.exists(SQLITE_PATH):
        return None
    try:
        conn = _sqlite_conn()
        c = conn.cursor()
        c.execute("SELECT data FROM planning_json WHERE id=1")
        row = c.fetchone()
        conn.close()
        if row:
            data = json.loads(row[0])
            return PlanningData.from_dict(data)
        return None
    except Exception:
        return None

def sqlite_last_updated() -> Optional[str]:
    """Retorna timestamp da última atualização no SQLite."""
    if not os.path.exists(SQLITE_PATH):
        return None
    try:
        conn = _sqlite_conn()
        c = conn.cursor()
        c.execute("SELECT updated_at FROM planning_json WHERE id=1")
        row = c.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


# ============================================
# WRAPPER data_editor — compatibilidade
# ============================================

def try_data_editor(df: pd.DataFrame, key: Optional[str] = None,
                    height: Optional[int] = None, column_config=None, **kwargs) -> Optional[pd.DataFrame]:
    """Wrapper robusto para st.data_editor — garante num_rows e disabled sempre passados."""
    if height is not None: kwargs.setdefault("height", height)
    if column_config is not None: kwargs.setdefault("column_config", column_config)
    # Garante que num_rows="dynamic" seja o padrão (permite adicionar/remover linhas)
    kwargs.setdefault("num_rows", "dynamic")

    editor_fn = getattr(st, "data_editor", None) or getattr(st, "experimental_data_editor", None)
    if editor_fn is None:
        st.dataframe(df)
        return df

    call_kwargs = dict(kwargs)
    if key is not None: call_kwargs["key"] = key

    # Tenta com todos os kwargs; se falhar por parâmetro desconhecido, remove um a um
    params_to_try_removing = ["disabled", "column_order", "hide_index"]
    try:
        return editor_fn(df, **call_kwargs)
    except TypeError as e:
        err = str(e)
        for param in params_to_try_removing:
            if param in err and param in call_kwargs:
                call_kwargs.pop(param)
                try:
                    return editor_fn(df, **call_kwargs)
                except Exception:
                    pass
        # último recurso: só df + key + height + num_rows
        minimal = {"num_rows": call_kwargs.get("num_rows", "dynamic"),
                   "use_container_width": call_kwargs.get("use_container_width", True)}
        if "height" in call_kwargs: minimal["height"] = call_kwargs["height"]
        if key: minimal["key"] = key
        try:
            return editor_fn(df, **minimal)
        except Exception:
            st.dataframe(df)
            return df
    except Exception:
        st.dataframe(df)
        return df


# ============================================
# HELPERS GRÁFICOS
# ============================================

PLOTLY_TEMPLATE = "plotly_dark"

def _fig_layout(fig, title="", height=380, xangle=-30):
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color=BK_DARK, family="Segoe UI")),
        template=PLOTLY_TEMPLATE,
        height=height,
        margin=dict(l=30, r=30, t=50, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="#1E293B",
        paper_bgcolor="#0F172A",
        font=dict(family="Segoe UI", color=BK_DARK),
    )
    if xangle:
        fig.update_xaxes(tickangle=xangle, gridcolor="#E0E7EF", showgrid=True)
    fig.update_yaxes(gridcolor="#E0E7EF", showgrid=True, zeroline=False)
    return fig

def fig_okr_monthly(okr: OKR) -> go.Figure:
    labels = _month_labels_for_okr(okr)
    prev  = [float(okr.meses[k].previsto)  if k < len(okr.meses) else 0.0 for k in range(36)]
    real  = [float(okr.meses[k].realizado) if k < len(okr.meses) else 0.0 for k in range(36)]
    diff  = [r - p for r, p in zip(real, prev)]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.7, 0.3],
        vertical_spacing=0.08,
        subplot_titles=[f"{okr.nome} — Previsto vs Realizado", "Diferença mensal"]
    )
    fig.add_trace(go.Bar(x=labels, y=prev, name="Previsto", marker_color=BK_BLUE_LIGHT, opacity=0.85), row=1, col=1)
    fig.add_trace(go.Bar(x=labels, y=real, name="Realizado", marker_color=BK_TEAL, opacity=0.9), row=1, col=1)

    y = np.array(real)
    x = np.arange(36)
    if np.count_nonzero(y) >= 3:
        z = np.polyfit(x, y, 1)
        trend = np.poly1d(z)(x).tolist()
        fig.add_trace(go.Scatter(x=labels, y=trend, mode="lines", name="Tendência",
                                 line=dict(color=BK_ORANGE, dash="dash", width=2)), row=1, col=1)

    colors = [BK_GREEN if d >= 0 else BK_RED for d in diff]
    fig.add_trace(go.Bar(x=labels, y=diff, name="Diferença", marker_color=colors, showlegend=False), row=2, col=1)

    fig.update_layout(
        barmode="group", height=520, template=PLOTLY_TEMPLATE,
        margin=dict(l=30, r=30, t=70, b=50), font=dict(family="Segoe UI", color=BK_DARK),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="#1E293B", paper_bgcolor="#0F172A",
    )
    fig.update_xaxes(tickangle=-45, gridcolor="#E0E7EF", row=2, col=1)
    fig.update_xaxes(gridcolor="#E0E7EF", row=1, col=1)
    fig.update_yaxes(gridcolor="#E0E7EF", zeroline=True, zerolinecolor="#ccc")
    return fig

def fig_okr_cumulative(okr: OKR) -> go.Figure:
    labels = _month_labels_for_okr(okr)
    prev = [float(okr.meses[k].previsto) if k < len(okr.meses) else 0.0 for k in range(36)]
    real = [float(okr.meses[k].realizado) if k < len(okr.meses) else 0.0 for k in range(36)]
    cum_p = np.cumsum(prev).tolist()
    cum_r = np.cumsum(real).tolist()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=cum_p, mode="lines", name="Acum. Planejado",
                             line=dict(color=BK_BLUE_LIGHT, width=2, dash="dot"),
                             fill="tozeroy", fillcolor="rgba(66,165,245,0.08)"))
    fig.add_trace(go.Scatter(x=labels, y=cum_r, mode="lines+markers", name="Acum. Realizado",
                             line=dict(color=BK_TEAL, width=2.5),
                             fill="tozeroy", fillcolor="rgba(0,137,123,0.1)"))
    return _fig_layout(fig, "Acumulado 36 meses", height=360)

def fig_okr_gauge(okr: OKR) -> go.Figure:
    prev_total = sum(m.previsto for m in okr.meses)
    real_total = sum(m.realizado for m in okr.meses)
    pct = (real_total / prev_total * 100) if prev_total > 0 else 0.0
    color = BK_GREEN if pct >= 95 else (BK_ORANGE if pct >= 70 else BK_RED)
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=pct,
        number={"suffix": "%", "font": {"size": 32, "family": "Segoe UI", "color": BK_DARK}},
        delta={"reference": 100, "valueformat": ".1f"},
        gauge={
            "axis": {"range": [0, 130], "tickwidth": 1, "tickcolor": BK_GRAY},
            "bar": {"color": color, "thickness": 0.35},
            "bgcolor": "white",
            "steps": [
                {"range": [0, 70],   "color": "rgba(229,57,53,0.08)"},
                {"range": [70, 95],  "color": "rgba(251,140,0,0.08)"},
                {"range": [95, 130], "color": "rgba(67,160,71,0.08)"},
            ],
            "threshold": {"line": {"color": BK_BLUE, "width": 3}, "thickness": 0.75, "value": 100},
        },
        title={"text": f"<b>% Realização</b><br><span style='font-size:11px'>{okr.nome[:40]}</span>",
               "font": {"family": "Segoe UI", "size": 13}},
    ))
    fig.update_layout(height=260, margin=dict(l=20, r=20, t=40, b=10),
                      paper_bgcolor="#0F172A")
    return fig

def fig_swot_quadrant(swot_items: List[SWOTItem]) -> go.Figure:
    quadrants = {"Força": (1, 1), "Fraqueza": (-1, 1), "Oportunidade": (1, -1), "Ameaça": (-1, -1)}
    quad_labels = {"Força": "FORÇAS", "Fraqueza": "FRAQUEZAS", "Oportunidade": "OPORTUNIDADES", "Ameaça": "AMEAÇAS"}
    priority_size = {"Alta": 20, "Média": 14, "Baixa": 10}

    fig = go.Figure()
    # Fundo dos quadrantes
    for tipo, (qx, qy) in quadrants.items():
        fig.add_shape(type="rect",
            x0=0 if qx > 0 else -1, y0=0 if qy > 0 else -1,
            x1=1 if qx > 0 else 0, y1=1 if qy > 0 else 0,
            fillcolor=SWOT_COLORS[tipo], opacity=0.06, line_width=0)
        fig.add_annotation(x=0.5*qx, y=0.88*qy, text=f"<b>{quad_labels[tipo]}</b>",
            showarrow=False, font=dict(size=12, color=SWOT_COLORS[tipo], family="Segoe UI"),
            xref="x", yref="y")

    # Contadores por tipo para posicionamento
    tipo_counter: Dict[str, int] = {}
    for item in swot_items:
        t = item.tipo
        if t not in quadrants: continue
        qx, qy = quadrants[t]
        n = tipo_counter.get(t, 0)
        tipo_counter[t] = n + 1
        col = n % 2
        row = n // 2
        x = qx * (0.2 + col * 0.35)
        y = qy * (0.65 - row * 0.28)
        size = priority_size.get(item.prioridade, 14)
        fig.add_trace(go.Scatter(
            x=[x], y=[y], mode="markers+text",
            marker=dict(size=size, color=SWOT_COLORS[t], opacity=0.85,
                        line=dict(width=1.5, color="white")),
            text=[item.descricao[:28] + ("…" if len(item.descricao) > 28 else "")],
            textposition="bottom center",
            textfont=dict(size=9, family="Segoe UI"),
            hovertext=f"<b>{item.tipo}</b> [{item.prioridade}]<br>{item.descricao}",
            hoverinfo="text",
            name=item.tipo,
            showlegend=False,
        ))

    # Linhas centrais
    fig.add_shape(type="line", x0=0, y0=-1, x1=0, y1=1, line=dict(color="#9E9E9E", width=1.5, dash="dot"))
    fig.add_shape(type="line", x0=-1, y0=0, x1=1, y1=0, line=dict(color="#9E9E9E", width=1.5, dash="dot"))

    fig.update_layout(
        xaxis=dict(range=[-1, 1], showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(range=[-1, 1], showgrid=False, zeroline=False, showticklabels=False),
        height=480, template="plotly_dark",
        margin=dict(l=10, r=10, t=50, b=10),
        paper_bgcolor="#0F172A",
        plot_bgcolor="#1E293B",
        title=dict(text="Matriz SWOT", font=dict(size=15, color=BK_DARK, family="Segoe UI")),
    )
    return fig

def fig_actions_status(planning: PlanningData) -> go.Figure:
    today = date.today()
    counts = {"Concluído": 0, "Em andamento": 0, "Pendente": 0, "Atrasado": 0}
    for a in planning.actions:
        if a.status == "Concluído":
            counts["Concluído"] += 1
        else:
            try:
                dv = datetime.strptime(a.data_vencimento, "%Y-%m-%d").date()
                if dv < today:
                    counts["Atrasado"] += 1
                elif a.status == "Em andamento":
                    counts["Em andamento"] += 1
                else:
                    counts["Pendente"] += 1
            except Exception:
                counts[a.status] = counts.get(a.status, 0) + 1

    labels = [k for k, v in counts.items() if v > 0]
    values = [counts[k] for k in labels]
    colors = [STATUS_COLORS[k] for k in labels]

    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        marker=dict(colors=colors, line=dict(color="white", width=2)),
        hole=0.55,
        textfont=dict(family="Segoe UI", size=12),
        hovertemplate="<b>%{label}</b><br>%{value} planos<br>%{percent}<extra></extra>",
    ))
    total = sum(values)
    fig.add_annotation(text=f"<b>{total}</b><br>total", x=0.5, y=0.5,
                       showarrow=False, font=dict(size=14, family="Segoe UI", color=BK_DARK))
    return _fig_layout(fig, "Status dos Planos de Ação", height=320, xangle=0)

def fig_actions_timeline(planning: PlanningData) -> go.Figure:
    if not planning.actions:
        return go.Figure()
    today = date.today()
    rows = []
    for a in planning.actions:
        try:
            d_ini = datetime.strptime(a.data_inicio, "%Y-%m-%d")
            d_fim = datetime.strptime(a.data_vencimento, "%Y-%m-%d")
        except Exception:
            d_ini = d_fim = datetime.now()
        status_eff = a.status
        if a.status != "Concluído" and d_fim.date() < today:
            status_eff = "Atrasado"
        rows.append(dict(Task=a.titulo[:30], Start=d_ini, Finish=d_fim,
                         Status=status_eff, Area=a.area, Responsavel=a.responsavel))
    df_gantt = pd.DataFrame(rows).sort_values("Start")

    fig = px.timeline(df_gantt, x_start="Start", x_end="Finish", y="Task",
                      color="Status", color_discrete_map=STATUS_COLORS,
                      hover_data={"Area": True, "Responsavel": True},
                      labels={"Task": "Plano", "Status": "Status"})
    hoje_str = datetime.now().strftime("%Y-%m-%d")
    fig.add_shape(type="line", x0=hoje_str, x1=hoje_str, y0=0, y1=1,
                  xref="x", yref="paper",
                  line=dict(color=BK_RED, width=2, dash="dash"))
    fig.add_annotation(x=hoje_str, y=1.02, xref="x", yref="paper",
                       text="<b>Hoje</b>", showarrow=False,
                       font=dict(color=BK_RED, size=11, family="Segoe UI"),
                       bgcolor="white", bordercolor=BK_RED, borderwidth=1)
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=10))
    return _fig_layout(fig, "Linha do Tempo (Gantt)", height=max(300, len(rows) * 32 + 80), xangle=-30)

def fig_okrs_overview(planning: PlanningData) -> go.Figure:
    if not planning.okrs:
        return go.Figure()
    names, totals_prev, totals_real, pcts = [], [], [], []
    for o in planning.okrs:
        tp = sum(m.previsto for m in o.meses)
        tr = sum(m.realizado for m in o.meses)
        names.append(o.nome[:25])
        totals_prev.append(tp)
        totals_real.append(tr)
        pcts.append((tr / tp * 100) if tp > 0 else 0.0)

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=names, y=totals_prev, name="Total Previsto",
                         marker_color=BK_BLUE_LIGHT, opacity=0.8), secondary_y=False)
    fig.add_trace(go.Bar(x=names, y=totals_real, name="Total Realizado",
                         marker_color=BK_TEAL, opacity=0.9), secondary_y=False)
    fig.add_trace(go.Scatter(x=names, y=pcts, mode="lines+markers+text",
                             name="% Realização", text=[f"{p:.0f}%" for p in pcts],
                             textposition="top center",
                             line=dict(color=BK_ORANGE, width=2.5),
                             marker=dict(size=8, color=BK_ORANGE)), secondary_y=True)

    fig.update_layout(barmode="group", height=400, template=PLOTLY_TEMPLATE,
                      font=dict(family="Segoe UI"), plot_bgcolor="#1E293B",
                      paper_bgcolor="#0F172A",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0.5, xanchor="center"),
                      margin=dict(l=30, r=30, t=60, b=60),
                      title=dict(text="OKRs — Visão Consolidada 36 meses",
                                 font=dict(size=15, color=BK_DARK)))
    fig.update_yaxes(title_text="Valor (unidade OKR)", gridcolor="#E0E7EF", secondary_y=False)
    fig.update_yaxes(title_text="% Realização", secondary_y=True)
    fig.update_xaxes(tickangle=-15, gridcolor="#E0E7EF")
    return fig


# ============================================
# HELPERS OKR
# ============================================

def _month_labels_for_okr(o: OKR) -> List[str]:
    labels = []
    ano = int(getattr(o, "inicio_ano", date.today().year) or date.today().year)
    mes = int(getattr(o, "inicio_mes", date.today().month) or date.today().month)
    for _ in range(36):
        labels.append(f"{mes:02d}/{ano}")
        mes += 1
        if mes > 12: mes, ano = 1, ano + 1
    return labels

def _okr_meta_df(pl: PlanningData) -> pd.DataFrame:
    rows = []
    for i, o in enumerate(pl.okrs, start=1):
        try: d0 = date(int(o.inicio_ano), int(o.inicio_mes), 1)
        except Exception: d0 = date.today().replace(day=1)
        rows.append({"okr_id": i, "OKR": o.nome, "Área": o.area,
                     "Unidade": o.unidade or "Inteiro", "Descrição": o.descricao,
                     "Início": d0, "Excluir": False})
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["okr_id","OKR","Área","Unidade","Descrição","Início","Excluir"])
    return df

def _sync_okrs_from_meta(pl: PlanningData, df_meta: pd.DataFrame) -> None:
    existing_by_id = {i+1: pl.okrs[i] for i in range(len(pl.okrs))}
    new_okrs: List[OKR] = []
    for _, r in df_meta.iterrows():
        if bool(r.get("Excluir", False)): continue
        nome = str(r.get("OKR","")).strip()
        if not nome: continue
        area = str(r.get("Área","")).strip()
        unidade = str(r.get("Unidade","Inteiro")).strip()
        desc = str(r.get("Descrição","")).strip()
        inicio = r.get("Início", None)
        if isinstance(inicio, (datetime, date)):
            inicio_ano, inicio_mes = int(inicio.year), int(inicio.month)
        else:
            inicio_ano, inicio_mes = date.today().year, date.today().month
        rid = r.get("okr_id", None)
        try: rid = int(rid)
        except Exception: rid = None
        if rid in existing_by_id:
            o = existing_by_id[rid]
            o.nome = nome; o.area = area; o.unidade = unidade; o.descricao = desc
            if (o.inicio_ano != inicio_ano) or (o.inicio_mes != inicio_mes):
                prev_vals = [m.previsto for m in o.meses][:36]
                real_vals = [m.realizado for m in o.meses][:36]
                o.inicio_ano, o.inicio_mes = inicio_ano, inicio_mes
                o.meses = []
                o.__post_init__()
                for k in range(min(36, len(o.meses))):
                    o.meses[k].previsto = float(prev_vals[k]) if k < len(prev_vals) else 0.0
                    o.meses[k].realizado = float(real_vals[k]) if k < len(real_vals) else 0.0
            new_okrs.append(o)
        else:
            o = OKR(nome=nome, area=area, unidade=unidade, descricao=desc,
                    inicio_ano=inicio_ano, inicio_mes=inicio_mes)
            o.__post_init__()
            new_okrs.append(o)
    pl.okrs = new_okrs

def _okr_wide_df(pl: PlanningData, kind: str) -> pd.DataFrame:
    rows = []
    for i, o in enumerate(pl.okrs, start=1):
        r = {"OKR": o.nome, "okr_id": i, "Unidade": o.unidade or "Inteiro"}
        for k in range(36):
            col = f"M{k+1:02d}"
            val = getattr(o.meses[k], kind) if k < len(o.meses) else 0.0
            r[col] = float(val) if pd.notna(val) else 0.0
        rows.append(r)
    cols = ["OKR","okr_id","Unidade"] + [f"M{k+1:02d}" for k in range(36)]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)

def _apply_wide_to_okrs(pl: PlanningData, df_wide: pd.DataFrame, kind: str) -> None:
    name_to_okr = {o.nome: o for o in pl.okrs}
    for _, r in df_wide.iterrows():
        okr_name = str(r.get("OKR","")).strip()
        if okr_name not in name_to_okr: continue
        o = name_to_okr[okr_name]
        unit = (o.unidade or "Inteiro").strip()
        for k in range(36):
            col = f"M{k+1:02d}"
            v = r.get(col, 0.0)
            try: fv = float(v) if v != "" else 0.0
            except Exception: fv = 0.0
            if unit.lower().startswith("inte"): fv = int(round(fv))
            if kind == "previsto": o.meses[k].previsto = fv
            else: o.meses[k].realizado = fv


# ============================================
# RELATÓRIO HTML
# ============================================

def format_brl(value: float) -> str:
    try:
        v = float(value)
        s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"R$ {s}"
    except Exception:
        return ""

def generate_recommendations_for_okr(okr: OKR, df: pd.DataFrame) -> List[str]:
    recs = []
    if df.empty: return ["Dados insuficientes para recomendações."]
    avg_prev = df['previsto'].mean() if not df['previsto'].isna().all() else 0
    avg_real = df['realizado'].mean() if not df['realizado'].isna().all() else 0
    if avg_prev == 0:
        recs.append("Definir metas previstas (previsto=0 impede análise).")
        return recs
    pct = ((avg_real - avg_prev) / avg_prev) * 100
    if pct < -10: recs.append("Realizado consistentemente abaixo do previsto (>10%). Revisar causas e replanejar.")
    elif pct < 0: recs.append("Leve subperformance. Reforçar acompanhamento semanal.")
    elif pct < 10: recs.append("Performance adequada. Padronizar processos e manter ritmo.")
    else: recs.append("Realizado acima do previsto — validar sustentabilidade e ajustar metas upward.")
    recs.append("Alinhar OKR com planos de ação e responsáveis com datas claras.")
    return recs

def suggest_okrs_from_data(planning: PlanningData, top_n: int = 5) -> List[str]:
    ideas = []
    areas = [a.area for a in planning.areas] if planning.areas else ["Comercial","Projetos Elétricos","Inovação e Tecnologia"]
    if any("forç" in s.tipo.lower() or "oportun" in s.tipo.lower() for s in planning.swot):
        ideas.append(f"Aumentar faturamento recorrente através de serviços de alto valor — Área: {areas[0]}")
    ideas.append("Reduzir lead time de propostas para <48 horas — Área: Comercial")
    ideas.append("Garantir 95% dos entregáveis no prazo em 12 meses — Área: Projetos Elétricos")
    ideas.append("Automatizar 3 relatórios mensais com integração BIM/Python — Área: Inovação e Tecnologia")
    ideas.append("Melhorar margem média por projeto em 5% até o fim do ano — Área: Comercial/Financeiro")
    return ideas[:top_n]

HTML_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Inter',sans-serif;background:#F0F4F8;color:#1a202c;}
.page-header{background:linear-gradient(135deg,#1565C0 0%,#00897B 100%);padding:36px 40px;color:#fff;}
.page-header h1{font-size:26px;font-weight:700;letter-spacing:-0.5px;}
.page-header p{font-size:13px;opacity:0.85;margin-top:6px;}
.content{padding:28px 32px;}
.card{background:#fff;border-radius:12px;padding:22px 26px;margin-bottom:20px;
      box-shadow:0 2px 12px rgba(0,0,0,0.06);border:1px solid #e2e8f0;}
.card h2{font-size:17px;font-weight:600;color:#1565C0;margin-bottom:14px;
          padding-bottom:8px;border-bottom:2px solid #E3F2FD;}
.card h3{font-size:14px;font-weight:600;color:#263238;margin:14px 0 6px;}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px;}
th{background:#E3F2FD;color:#1565C0;font-weight:600;padding:9px 12px;text-align:left;}
td{padding:8px 12px;border-bottom:1px solid #f1f5f9;color:#374151;}
tr:hover td{background:#f8fafc;}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:600;}
.badge-alta{background:#FEE2E2;color:#DC2626;}
.badge-media{background:#FEF3C7;color:#D97706;}
.badge-baixa{background:#D1FAE5;color:#059669;}
.badge-forca{background:#D1FAE5;color:#059669;}
.badge-fraqueza{background:#FEE2E2;color:#DC2626;}
.badge-oportunidade{background:#DBEAFE;color:#1D4ED8;}
.badge-ameaca{background:#FEF3C7;color:#D97706;}
.kpi-row{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px;}
.kpi{background:#F0F9FF;border:1px solid #BAE6FD;border-radius:10px;padding:14px 20px;
     flex:1;min-width:140px;text-align:center;}
.kpi-val{font-size:24px;font-weight:700;color:#0369A1;}
.kpi-label{font-size:11px;color:#64748B;margin-top:2px;}
.okr-chart{text-align:center;margin:16px 0;}
.footer{text-align:center;padding:20px;font-size:12px;color:#94A3B8;
        border-top:1px solid #e2e8f0;margin-top:20px;}
.rec-item{background:#EFF6FF;border-left:3px solid #1565C0;padding:8px 14px;
          border-radius:0 6px 6px 0;margin-bottom:6px;font-size:13px;}
</style>
"""

def build_html_report(planning: PlanningData) -> str:
    parts = [f"""<!DOCTYPE html><html lang="pt-br"><head><meta charset="utf-8">
<title>Relatório BK — Planejamento Estratégico</title>{HTML_CSS}</head><body>
<div class="page-header">
  <h1>BK Engenharia e Tecnologia — Planejamento Estratégico (3 anos)</h1>
  <p>Relatório gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}</p>
</div><div class="content">"""]

    # KPIs executivos
    total_prev = sum(m.previsto for o in planning.okrs for m in o.meses)
    total_real = sum(m.realizado for o in planning.okrs for m in o.meses)
    pct_geral = (total_real / total_prev * 100) if total_prev > 0 else 0
    today = date.today()
    atrasados = sum(1 for a in planning.actions
                    if a.status != "Concluído"
                    and _safe_date(a.data_vencimento) and _safe_date(a.data_vencimento) < today)
    concluidos = sum(1 for a in planning.actions if a.status == "Concluído")

    parts.append(f"""<div class="card">
<h2>🎯 Painel Executivo</h2>
<div class="kpi-row">
  <div class="kpi"><div class="kpi-val">{len(planning.okrs)}</div><div class="kpi-label">OKRs ativos</div></div>
  <div class="kpi"><div class="kpi-val">{len(planning.actions)}</div><div class="kpi-label">Planos de ação</div></div>
  <div class="kpi"><div class="kpi-val">{concluidos}</div><div class="kpi-label">Planos concluídos</div></div>
  <div class="kpi"><div class="kpi-val" style="color:{'#DC2626' if atrasados else '#059669'}">{atrasados}</div><div class="kpi-label">Planos atrasados</div></div>
  <div class="kpi"><div class="kpi-val">{pct_geral:.1f}%</div><div class="kpi-label">Realização geral</div></div>
</div></div>""")

    # Estratégia
    s = planning.strategic
    if any([s.visao, s.missao, s.valores, s.pilares]):
        parts.append(f"""<div class="card"><h2>🧭 Norte Estratégico</h2>
<table><tr><th>Campo</th><th>Conteúdo</th></tr>
{''.join(f'<tr><td><b>{label}</b></td><td>{val}</td></tr>' for label, val in [
    ("Visão", s.visao), ("Missão", s.missao), ("Valores", s.valores),
    ("Proposta de Valor", s.proposta_valor), ("Público-alvo", s.publico_alvo),
    ("Pilares", s.pilares), ("Diferenciais", s.diferenciais),
    ("Objetivos Estratégicos", s.objetivos_estrategicos)
] if val)}
</table></div>""")

    #  Sócios e Equipe Liderança
    
    if planning.partners:
        parts.append('<div class="card"><h2>👥 Sócios/Gestores</h2><table><tr><th>Nome</th><th>Cargo</th><th>E-mail</th><th>Telefone</th></tr>')
        for p in planning.partners:
            parts.append(f"<tr><td><b>{p.nome}</b></td><td>{p.cargo}</td><td>{p.email}</td><td>{p.telefone}</td></tr>")
        parts.append("</table></div>")

    # Áreas
    if planning.areas:
        parts.append('<div class="card"><h2>🏢 Áreas e Responsáveis</h2><table><tr><th>Área</th><th>Responsável</th><th>E-mail</th><th>Observações</th></tr>')
        for a in planning.areas:
            parts.append(f"<tr><td><b>{a.area}</b></td><td>{a.responsavel}</td><td>{a.email}</td><td>{a.observacoes}</td></tr>")
        parts.append("</table></div>")

    # SWOT
    if planning.swot:
        parts.append('<div class="card"><h2>⚖️ Análise SWOT</h2><table><tr><th>Tipo</th><th>Prioridade</th><th>Descrição</th></tr>')
        for s in planning.swot:
            badge_t = s.tipo.lower().replace("ç","c").replace("ã","a")
            badge_p = s.prioridade.lower()
            parts.append(f'<tr><td><span class="badge badge-{badge_t}">{s.tipo}</span></td>'
                         f'<td><span class="badge badge-{badge_p}">{s.prioridade}</span></td>'
                         f'<td>{s.descricao}</td></tr>')
        parts.append("</table></div>")

    # OKRs
    if planning.okrs:
        parts.append('<div class="card"><h2>📈 OKRs (36 meses)</h2>')
        # Gráfico agregado
        try:
            fig_agg = fig_okrs_overview(planning)
            img = fig_agg.to_image(format="png", width=1100, height=400)
            parts.append(f'<div class="okr-chart"><img src="data:image/png;base64,{base64.b64encode(img).decode()}" style="max-width:100%"/></div>')
        except Exception:
            pass

        for o in planning.okrs:
            df = okr_to_dataframe(o)
            tp = df['previsto'].sum(); tr = df['realizado'].sum()
            pct_o = (tr / tp * 100) if tp > 0 else 0
            parts.append(f'<h3>📊 {o.nome}</h3>')
            parts.append(f'<p style="font-size:12px;color:#64748B">Área: <b>{o.area}</b> | Unidade: <b>{o.unidade}</b> | Início: <b>{o.inicio_mes:02d}/{o.inicio_ano}</b> | Realização: <b>{pct_o:.1f}%</b></p>')
            if o.descricao: parts.append(f'<p style="margin:6px 0;font-size:13px">{o.descricao}</p>')
            # Gráfico mensal
            try:
                fig_m = fig_okr_monthly(o)
                img_m = fig_m.to_image(format="png", width=1100, height=520)
                parts.append(f'<div class="okr-chart"><img src="data:image/png;base64,{base64.b64encode(img_m).decode()}" style="max-width:100%"/></div>')
            except Exception:
                pass
            # Tabela mensal
            parts.append('<table><tr><th>M#</th><th>Mês/Ano</th><th>Previsto</th><th>Realizado</th><th>Δ</th></tr>')
            labels = _month_labels_for_okr(o)
            for i, m in enumerate(o.meses):
                vp = format_brl(m.previsto) if "R$" in (o.unidade or "") else f"{m.previsto:,.2f}"
                vr = format_brl(m.realizado) if "R$" in (o.unidade or "") else f"{m.realizado:,.2f}"
                delta = m.realizado - m.previsto
                delta_str = f'+{delta:,.2f}' if delta >= 0 else f'{delta:,.2f}'
                color = "#059669" if delta >= 0 else "#DC2626"
                parts.append(f'<tr><td>{i+1}</td><td>{labels[i]}</td><td>{vp}</td><td>{vr}</td>'
                              f'<td style="color:{color};font-weight:600">{delta_str}</td></tr>')
            parts.append("</table>")
            # Recomendações
            recs = generate_recommendations_for_okr(o, df)
            parts.append('<div style="margin-top:12px">')
            for rec in recs:
                parts.append(f'<div class="rec-item">💡 {rec}</div>')
            parts.append("</div>")
        parts.append("</div>")

    # Planos de Ação
    if planning.actions:
        parts.append('<div class="card"><h2>✅ Planos de Ação</h2>')
        try:
            fig_st = fig_actions_status(planning)
            img_st = fig_st.to_image(format="png", width=600, height=320)
            parts.append(f'<div class="okr-chart"><img src="data:image/png;base64,{base64.b64encode(img_st).decode()}" style="max-width:60%"/></div>')
        except Exception:
            pass
        parts.append('<table><tr><th>Título</th><th>Área</th><th>Responsável</th><th>Início</th><th>Vencimento</th><th>Status</th><th>Atraso</th></tr>')
        for ac in planning.actions:
            dv = _safe_date(ac.data_vencimento)
            atraso = max(0, (today - dv).days) if (dv and ac.status != "Concluído" and dv < today) else 0
            badge_s = {"Concluído":"#059669","Em andamento":"#D97706","Pendente":"#64748B"}.get(ac.status,"#64748B")
            atraso_str = f'<span style="color:#DC2626;font-weight:600">{atraso}d</span>' if atraso > 0 else "—"
            parts.append(f'<tr><td><b>{ac.titulo}</b></td><td>{ac.area}</td><td>{ac.responsavel}</td>'
                         f'<td>{ac.data_inicio}</td><td>{ac.data_vencimento}</td>'
                         f'<td><span style="color:{badge_s};font-weight:600">{ac.status}</span></td>'
                         f'<td>{atraso_str}</td></tr>')
        parts.append("</table></div>")

    parts.append('<div class="footer">Produzido por BK Engenharia e Tecnologia</div>')
    parts.append("</div></body></html>")
    return "\n".join(parts)

def _safe_date(s: str) -> Optional[date]:
    try: return datetime.strptime(str(s), "%Y-%m-%d").date()
    except Exception: return None

def _actions_df(pl: PlanningData) -> pd.DataFrame:
    rows = []
    today = date.today()
    from datetime import timedelta
    for a in pl.actions:
        dv = _safe_date(a.data_vencimento)
        status_eff = a.status
        # Semáforo: 🟢 concluído, 🟡 falta ≤2 dias, 🔴 atrasado, ⚪ normal
        if a.status == "Concluído":
            semaforo = "🟢"
        elif dv and dv < today:
            status_eff = "Atrasado"
            semaforo = "🔴"
        elif dv and (dv - today).days <= 2:
            semaforo = "🟡"
        else:
            semaforo = "⚪"
        rows.append({"": semaforo, "Título": a.titulo, "OKR": getattr(a,"okr",""),
                     "Área": a.area, "Responsável": a.responsavel,
                     "Descrição": a.descricao, "Como Fazer": getattr(a,"como_fazer",""),
                     "Início": a.data_inicio if hasattr(a, "data_inicio") else "",
                     "Vencimento": a.data_vencimento, "Status": a.status,
                     "Status Efetivo": status_eff, "Observações": a.observacoes, "Excluir": False})
    cols = ["","Título","OKR","Área","Responsável","Descrição","Como Fazer","Início","Vencimento","Status","Status Efetivo","Observações","Excluir"]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)

def _sync_actions(pl: PlanningData, df: pd.DataFrame) -> None:
    def _fmt_date(val) -> str:
        """Converte datetime.date, pd.Timestamp ou string para YYYY-MM-DD."""
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return date.today().strftime("%Y-%m-%d")
        if isinstance(val, (datetime, pd.Timestamp)):
            return val.strftime("%Y-%m-%d")
        if isinstance(val, date):
            return val.strftime("%Y-%m-%d")
        s = str(val).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return date.today().strftime("%Y-%m-%d")

    new_actions = []
    for _, r in df.iterrows():
        if bool(r.get("Excluir", False)):
            continue
        titulo = str(r.get("Título","")).strip()
        if not titulo:
            continue
        new_actions.append(PlanoAcao(
            titulo=titulo,
            area=str(r.get("Área","")).strip(),
            responsavel=str(r.get("Responsável","")).strip(),
            descricao=str(r.get("Descrição","")).strip(),
            data_inicio=_fmt_date(r.get("Início")),
            data_vencimento=_fmt_date(r.get("Vencimento")),
            status=str(r.get("Status","Pendente")).strip(),
            observacoes=str(r.get("Observações","")).strip(),
            okr=str(r.get("OKR","")).strip(),
            como_fazer=str(r.get("Como Fazer","")).strip(),
        ))
    pl.actions = new_actions


# ============================================
# APP CONFIG & CSS GLOBAL
# ============================================

st.set_page_config(
    page_title="BK Planejamento Estratégico",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Resolve a connection string agora que st.secrets está disponível
DB_CONN_STR = _get_db_conn_str()

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap');

/* ── Base ── */
html, body, [class*="css"] {
    font-family: 'Inter', system-ui, -apple-system, sans-serif !important;
    color: #F1F5F9 !important;
}
.stApp { background: #0F172A !important; }

section.main > div.block-container {
    max-width: 1360px;
    padding-top: 0.5rem;
    padding-bottom: 2rem;
}

/* ── Sidebar dark ── */
section[data-testid="stSidebar"],
section[data-testid="stSidebar"] > div {
    background: linear-gradient(180deg, #0F172A 0%, #1E293B 100%) !important;
    border-right: 1px solid rgba(30,64,175,0.3) !important;
}
section[data-testid="stSidebar"] * { color: #F1F5F9 !important; }
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 { color: #93C5FD !important; }
section[data-testid="stSidebar"] .stButton > button {
    background: linear-gradient(135deg, #1E40AF, #2563EB) !important;
    color: #fff !important;
    border: none !important;
    border-radius: 8px !important;
    width: 100%;
    margin-bottom: 4px;
    font-weight: 600 !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    box-shadow: 0 4px 14px rgba(37,99,235,0.4) !important;
}

/* ── Header hero ── */
.bk-hero {
    background: linear-gradient(135deg, #1E3A8A 0%, #1E40AF 50%, #2563EB 100%);
    padding: 22px 32px;
    border-radius: 16px;
    margin-bottom: 20px;
    color: #fff;
    box-shadow: 0 8px 32px rgba(59,130,246,0.2);
    border: 1px solid rgba(147,197,253,0.15);
    display: flex;
    align-items: center;
    gap: 18px;
}
.bk-hero-logo {
    width: 54px; height: 54px; border-radius: 14px;
    background: rgba(255,255,255,0.12);
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; overflow: hidden;
}
.bk-hero-logo img { width: 100%; height: 100%; object-fit: cover; border-radius: 12px; }
.bk-hero h1 { font-size: 22px; font-weight: 900; margin: 0; letter-spacing: -0.02em; }
.bk-hero p  { font-size: 12px; opacity: 0.75; margin: 4px 0 0; }

/* ── KPI cards ── */
.kpi-card {
    background: #1E293B;
    border-radius: 14px;
    padding: 16px 20px;
    text-align: center;
    box-shadow: 0 2px 12px rgba(0,0,0,0.3);
    border: 1px solid #334155;
}
.kpi-card .val { font-size: 28px; font-weight: 900; color: #93C5FD; }
.kpi-card .lbl { font-size: 11px; color: #94A3B8; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.05em; }

/* ── Section title ── */
.section-title {
    font-size: 15px; font-weight: 700; color: #93C5FD !important;
    padding: 6px 0 10px;
    border-bottom: 2px solid rgba(59,130,246,0.25);
    margin-bottom: 14px;
}

/* ── Tabs ── */
div[data-baseweb="tab-list"] {
    background: rgba(30,64,175,0.15) !important;
    border-radius: 12px !important;
    padding: 4px !important;
    gap: 2px !important;
}
div[data-baseweb="tab"] {
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 13px !important;
    padding: 7px 16px !important;
    color: #94A3B8 !important;
    transition: all 0.2s !important;
}
div[data-baseweb="tab"][aria-selected="true"] {
    background: linear-gradient(135deg, #1E40AF, #2563EB) !important;
    color: #fff !important;
    box-shadow: 0 2px 10px rgba(37,99,235,0.35) !important;
}

/* ── Inputs ── */
div[data-baseweb="input"] > div,
div[data-baseweb="textarea"] > div {
    background: #1E293B !important;
    border: 1.5px solid #334155 !important;
    border-radius: 8px !important;
}
div[data-baseweb="input"] > div:focus-within,
div[data-baseweb="textarea"] > div:focus-within {
    border-color: #3B82F6 !important;
    box-shadow: 0 0 0 3px rgba(59,130,246,0.2) !important;
}
div[data-baseweb="input"] input,
div[data-baseweb="textarea"] textarea {
    color: #F1F5F9 !important;
    background: #1E293B !important;
    font-size: 14px !important;
}
div[data-baseweb="select"] > div:first-child {
    background: #1E293B !important;
    border: 1.5px solid #334155 !important;
    border-radius: 8px !important;
    color: #F1F5F9 !important;
}

/* ── Labels — claros e legíveis ── */
div[data-testid="stTextInput"] label,
div[data-testid="stTextArea"] label,
div[data-testid="stSelectbox"] label,
div[data-testid="stNumberInput"] label,
div[data-testid="stDateInput"] label,
div[data-testid="stMultiSelect"] label,
div[data-testid="stCheckbox"] label,
div[data-testid="stRadio"] label,
div[data-testid="stSlider"] label {
    color: #CBD5E1 !important;
    font-weight: 500 !important;
    font-size: 13px !important;
}

/* ── Botões ── */
.stButton > button {
    border-radius: 9px !important;
    font-weight: 600 !important;
    font-size: 13px !important;
    transition: all 0.2s !important;
    border: 1px solid #334155 !important;
    color: #F1F5F9 !important;
    background: #1E293B !important;
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #1E40AF, #2563EB) !important;
    border: none !important;
    color: #fff !important;
    box-shadow: 0 2px 10px rgba(37,99,235,0.3) !important;
}
.stButton > button[kind="primary"]:hover {
    box-shadow: 0 4px 18px rgba(37,99,235,0.45) !important;
    transform: translateY(-1px) !important;
}
.stButton > button:not([kind="primary"]):hover {
    background: #334155 !important;
    border-color: #3B82F6 !important;
}

/* ── Métricas nativas ── */
[data-testid="metric-container"] {
    background: #1E293B;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 14px 18px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.25);
}
[data-testid="metric-container"] [data-testid="stMetricLabel"] {
    font-size: 11px !important; font-weight: 700 !important;
    text-transform: uppercase; letter-spacing: 0.05em; color: #94A3B8 !important;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] {
    font-size: 22px !important; font-weight: 900 !important; color: #93C5FD !important;
}

/* ── DataEditor ── */
[data-testid="stDataEditor"],
[data-testid="stDataFrame"] {
    border-radius: 10px; overflow: hidden; border: 1px solid #334155;
}

/* ── Expander ── */
details > summary {
    background: #1E293B !important;
    border: 1px solid #334155 !important;
    border-radius: 9px !important;
    padding: 9px 14px !important;
    font-weight: 600 !important;
    color: #93C5FD !important;
}

/* ── Caption ── */
[data-testid="stCaptionContainer"] { color: #94A3B8 !important; font-size: 12px !important; }

/* ── Markdown h1-h4 e texto ── */
h1, h2, h3, h4 { color: #F1F5F9 !important; }
p, li, span { color: #CBD5E1 !important; }
code { background: #334155 !important; color: #93C5FD !important; border-radius: 4px; padding: 2px 6px; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: #0F172A; }
::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #3B82F6; }
</style>
""", unsafe_allow_html=True)


# ============================================
# AUTENTICAÇÃO — LOGIN
# ============================================

import hashlib as _hashlib

_USERS = {
    "marcio@bk-engenharia.com": _hashlib.sha256("velhodomal1976".encode()).hexdigest(),
}

def _check_credentials(email: str, password: str) -> bool:
    h = _hashlib.sha256(password.encode()).hexdigest()
    return _USERS.get(email.strip().lower()) == h

def _render_login():
    """Renderiza a tela de login centralizada. Retorna True se autenticado."""
    if st.session_state.get("authenticated"):
        return True

    import base64 as _b64
    try:
        _icon_data = open("bk_icon.jpeg", "rb").read()
        _icon_uri = f"data:image/jpeg;base64,{_b64.b64encode(_icon_data).decode()}"
    except Exception:
        _icon_uri = ""

    # Centralizar verticalmente com espaço
    st.markdown("<br><br>", unsafe_allow_html=True)

    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        # Card de login
        st.markdown(f"""
        <div style="background:#1E293B; border:1px solid #334155; border-radius:18px;
                    padding:36px 32px; box-shadow:0 8px 40px rgba(0,0,0,0.4); text-align:center;">
            {"<img src='" + _icon_uri + "' style='width:72px;height:72px;border-radius:16px;margin-bottom:16px;'/>" if _icon_uri else ""}
            <div style="font-size:20px;font-weight:900;color:#F1F5F9;letter-spacing:-0.02em;">
                BK Planejamento Estratégico
            </div>
            <div style="font-size:12px;color:#94A3B8;margin-top:4px;margin-bottom:28px;">
                BK Engenharia e Tecnologia
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        email = st.text_input("E-mail", placeholder="seu@email.com", key="_login_email")
        password = st.text_input("Senha", type="password", placeholder="••••••••", key="_login_pass")

        if st.button("Entrar", type="primary", use_container_width=True, key="_login_btn"):
            if _check_credentials(email, password):
                st.session_state["authenticated"] = True
                st.session_state["user_email"] = email.strip().lower()
                st.rerun()
            else:
                st.error("E-mail ou senha incorretos.")

        st.markdown("""
        <div style="text-align:center;margin-top:20px;font-size:11px;color:#475569;">
            Acesso restrito — BK Engenharia e Tecnologia
        </div>
        """, unsafe_allow_html=True)

    return False

# Bloqueia o app inteiro se não autenticado
if not _render_login():
    st.stop()

# Botão de logout na sidebar
with st.sidebar:
    _user_email = st.session_state.get("user_email", "")
    st.markdown(f"""
    <div style="background:#1E293B;border:1px solid #334155;border-radius:10px;
                padding:10px 14px;margin-bottom:12px;font-size:12px;color:#94A3B8;">
        🔐 <b style="color:#93C5FD">{_user_email}</b>
    </div>
    """, unsafe_allow_html=True)
    if st.button("🚪 Sair", key="_logout_btn", use_container_width=True):
        st.session_state["authenticated"] = False
        st.session_state["user_email"] = ""
        st.rerun()
    st.markdown("---")


# ============================================
# LOAD STATE — AGORA COM CARGA DO BANCO
# ============================================

if "planning" not in st.session_state:
    planning = None
    _neon_load_error = None

    # ── PRIORIDADE 1: Neon PostgreSQL ──────────────────────────────────────────
    # No Streamlit Cloud o filesystem é EFÊMERO: SQLite some a cada restart/sleep.
    # Neon é o único storage verdadeiramente persistente — deve ser carregado primeiro.
    try:
        planning = load_from_postgres(DB_CONN_STR)
    except Exception as _e:
        _neon_load_error = str(_e)
        planning = None

    # ── PRIORIDADE 2: SQLite local (cache — útil em dev local ou deploy custom) ─
    if planning is None:
        planning = load_from_sqlite()

    # ── PRIORIDADE 3: planning.json legado ────────────────────────────────────
    if planning is None and os.path.exists(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "planning.json")):
        try:
            _json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "planning.json")
            with open(_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            planning = PlanningData.from_dict(data)
            save_to_sqlite(planning)  # cache local
        except Exception:
            planning = None

    # ── ÚLTIMO RECURSO: dados vazios ──────────────────────────────────────────
    if planning is None:
        planning = PlanningData()

    st.session_state.planning = planning
    st.session_state["_neon_load_error"] = _neon_load_error

planning: PlanningData = st.session_state.planning

def save_planning(pl: PlanningData):
    """Salva no session_state, persiste em SQLite (cache) e sincroniza com Neon (principal)."""
    st.session_state.planning = pl

    # ── Neon PostgreSQL (storage principal — único persistente no Streamlit Cloud) ──
    msg_neon = export_to_postgres(pl, DB_CONN_STR)
    st.session_state["_last_neon_save"] = msg_neon
    if msg_neon and "❌" in msg_neon:
        st.error(
            f"❌ **ATENÇÃO: Dados NÃO foram salvos na nuvem!**\n\n"
            f"Erro Neon: `{msg_neon}`\n\n"
            f"Verifique a aba **☁️ Diagnóstico** na barra lateral."
        )
    # ── SQLite local (cache — perde no restart do Streamlit Cloud) ──
    msg_sqlite = save_to_sqlite(pl)
    if "❌" in msg_sqlite:
        st.warning(f"⚠️ Cache local (SQLite): {msg_sqlite}")


# ============================================
# HEADER
# ============================================

st.markdown(f"""
<div class="bk-hero">
    <div class="bk-hero-logo"><img src="data:image/jpeg;base64,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QhDzzyQ0003/APADDVZvP72jJd6y6w0jDDCCCCCCDD//AP8A/wD/AD//AM0ww1wADDEAEEP8G+gAADuzGbcjDzzgAAEEHzo7zh3EEHH/AKDCDDTvDPKJkZx3/DBBBBDCCOOKCDDT9/8A/f8A/wD/APffffQQQQQQQQ/xd6AAABNDA52rDDDDDTTXPgE/KKQQQQQgghqwg0vs+V6w8/ji9/v/AL7777777/8AvPLPPPPPPPPPPP8A/wD/AP8A/wD8v9WIAAAAPjC8P47Dzz3HE1TwwNOikAAEELf/APvKGe1M/wCssritvv2sssssssssogss/wD/AP73/wD88/8A88P88OPHG4ICEAAABR8Xe5LzwwwzzzygLw8vMc//AP8Aiw88wnuTPf5n/wD9/wDODzyyyyyy2yyyyyymOOOeuOOe+OOOyyy+951KEowAAAAQc6R+OOQww89xxseV/vD3uOOS3PPNENc+yu//AP8A/wD/AP8A+8sMMMc847777764444444444oII764444p6+SAAAAAAPzcw47YwwkAABLan/wD/ACyggt72xFzjjj//AP8A/wD61/8A/wD/ALz3/wD/AP8A/wDuIYJf/wC++++++++uOe++/wDvvOcm7AAAAAAADXMMsvr0PPTSAk/w/wDr77/K4BEI/wD/AP8A/wD/AONPPL28sM89/wD/AP8A/wDMf/8A/bzzzzzzzjH/APvvvhDKBjHXlYAAAAAAAAPj0ys873Pvvig9zwH3TzocIwwgggwggwwww8cMsvv/AK45/wD/AP8ArPPMILb/AP8A/wD/AP8Afb3ffbfPstsT7oAAAAAAAAAEOSG5wda8svrgF/HPfu2B3AwwwwQQwQQQQVf7www8tvvvvuogggggww8vvv8A77rHf/333zqIJlTAAAAAAAAAAAADTi+FH6445T0lgROleFoc3333333333kEEEEUwwwAEEEEI477/P8A/vOCCCCOPNNPRx14C+zvAAAAAAAAAAAAAAAQVGFcCz79F8KUy38ITjP/AP8AjDIIYYAEXPHjjHrrb64wzjYYI7Pf+J7bX3n/ANdxww0Cl+KQgAAAAAAAAAAAAAAAAAA0rf8AfXTT/AMMNPPPPPPjuoAANPPPPPLCBDPPPPOMNPAAPLPHPPLHLDDHPPPPLL+AAAAAAAAAAAAAAAAAAAAAAAAA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" alt="BK"/></div>
    <div>
        <h1>BK Planejamento Estratégico</h1>
        <p>Planejamento Estratégico 36 meses — BK Engenharia e Tecnologia</p>
    </div>
</div>
""", unsafe_allow_html=True)

# Banner de alerta se Neon estava inacessível ao carregar
_neon_load_err = st.session_state.get("_neon_load_error")
if _neon_load_err:
    st.error(
        "⚠️ **Banco de dados Neon inacessível ao iniciar.** "
        "Os dados exibidos podem estar desatualizados ou vazios. "
        "Use **🔄 Forçar Sync** na barra lateral após verificar a conexão."
    )


# ============================================
# SIDEBAR
# ============================================

with st.sidebar:
    st.markdown("## 📁 Arquivo")
    uploaded = st.file_uploader("Importar JSON", type=["json"], key="sidebar_uploader",
                                label_visibility="collapsed")
    if uploaded:
        try:
            data = json.load(uploaded)
            st.session_state.planning = PlanningData.from_dict(data)
            planning = st.session_state.planning
            save_planning(planning)
            st.success("JSON importado e salvo!")
        except Exception as e:
            st.error(f"Erro: {e}")

    st.markdown("---")
    st.markdown("## 📦 Exportar")

    xlsx = export_to_excel_bytes(planning)
    st.download_button("📊 Baixar Excel", data=xlsx, key="dl_xlsx_sb",
        file_name="planning_multi_sheet.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True)

    st.download_button("⬇️ Exportar JSON", key="dl_json",
        data=json.dumps(planning.to_dict(), ensure_ascii=False, indent=2).encode("utf-8"),
        file_name="planning_export.json", mime="application/json",
        use_container_width=True)

    st.markdown("---")
    st.markdown("## 💾 Status & Diagnóstico")

    # ── Status Neon ──
    _neon_ok = False
    _neon_err_detail = ""
    try:
        _eng = _make_pg_engine(DB_CONN_STR)
        with _eng.connect() as _c:
            _c.execute(text("SELECT 1"))
        _neon_ok = True
        st.success("☁️ Neon: 🟢 Conectado")
    except Exception as _e:
        _neon_err_detail = str(_e)
        st.error(f"☁️ Neon: 🔴 Offline")
        with st.expander("Ver erro completo"):
            st.code(_neon_err_detail, language="text")

    # ── Status SQLite ──
    _ts = sqlite_last_updated()
    if _ts:
        st.caption(f"🗄️ Cache local: ✅ {_ts}")
    else:
        st.caption("🗄️ Cache local: vazio (normal no Streamlit Cloud)")

    # ── Aviso se carregamento falhou ──
    _neon_load_err = st.session_state.get("_neon_load_error")
    if _neon_load_err:
        st.warning("⚠️ Neon indisponível ao iniciar — dados podem estar desatualizados.")

    # ── Botão Force Sync ──
    if st.button("🔄 Forçar Sync → Neon", key="force_sync_neon",
                 use_container_width=True, type="primary"):
        _sync_msg = export_to_postgres(st.session_state.planning, DB_CONN_STR)
        if "✅" in _sync_msg:
            st.success(_sync_msg)
        else:
            st.error(_sync_msg)

    # ── Botão recarregar do Neon ──
    if st.button("⬇️ Recarregar do Neon", key="reload_neon",
                 use_container_width=True):
        _reloaded = load_from_postgres(DB_CONN_STR)
        if _reloaded:
            st.session_state.planning = _reloaded
            st.success("✅ Dados recarregados do Neon!")
            st.rerun()
        else:
            st.error("Neon vazio ou inacessível.")

    st.markdown("---")
    st.markdown("## 💡 Sugestões de OKRs")
    for i, item in enumerate(suggest_okrs_from_data(planning), 1):
        st.caption(f"{i}. {item}")


# ============================================
# DASHBOARD KPIs
# ============================================

today = date.today()
total_prev_geral = sum(m.previsto for o in planning.okrs for m in o.meses)
total_real_geral = sum(m.realizado for o in planning.okrs for m in o.meses)
pct_real_geral = (total_real_geral / total_prev_geral * 100) if total_prev_geral > 0 else 0
n_atrasados = sum(1 for a in planning.actions
                  if a.status != "Concluído" and _safe_date(a.data_vencimento)
                  and _safe_date(a.data_vencimento) < today)
n_concluidos = sum(1 for a in planning.actions if a.status == "Concluído")
n_andamento  = sum(1 for a in planning.actions if a.status == "Em andamento")

col1, col2, col3, col4, col5 = st.columns(5)
def kpi_html(val, label, color="#93C5FD"):
    return f"""<div class="kpi-card">
    <div class="val" style="color:{color}">{val}</div>
    <div class="lbl">{label}</div></div>"""

col1.markdown(kpi_html(len(planning.okrs), "OKRs"), unsafe_allow_html=True)
col2.markdown(kpi_html(f"{pct_real_geral:.1f}%", "Realização Geral",
              BK_GREEN if pct_real_geral >= 90 else (BK_ORANGE if pct_real_geral >= 70 else BK_RED)), unsafe_allow_html=True)
col3.markdown(kpi_html(len(planning.actions), "Planos de Ação"), unsafe_allow_html=True)
col4.markdown(kpi_html(n_concluidos, "Concluídos", BK_GREEN), unsafe_allow_html=True)
col5.markdown(kpi_html(n_atrasados, "Atrasados", BK_RED if n_atrasados > 0 else BK_GREEN), unsafe_allow_html=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)


# ============================================
# TABS
# ============================================

tabs = st.tabs([
    "🏠 Dashboard",
    "👥 Sócios/Gestores",
    "🧭 Estratégia",
    "🏢 Áreas",
    "⚖️ SWOT",
    "📈 OKRs",
    "✅ Planos de Ação",
    "📄 Relatórios"
])


# ══════════════ TAB 0: DASHBOARD ══════════════
with tabs[0]:
    st.markdown('<div class="section-title">Visão Geral Executiva</div>', unsafe_allow_html=True)

    if planning.okrs:
        fig_ov = fig_okrs_overview(planning)
        st.plotly_chart(fig_ov, use_container_width=True, key="dash_okr_overview")

        # Gauges de OKRs
        st.markdown("**Performance por OKR**")
        gauge_cols = st.columns(min(len(planning.okrs), 4))
        for i, o in enumerate(planning.okrs[:4]):
            with gauge_cols[i % 4]:
                st.plotly_chart(fig_okr_gauge(o), use_container_width=True, key=f"dash_gauge_{i}")
    else:
        st.info("Cadastre OKRs na aba **📈 OKRs** para visualizar o dashboard.")

    c1, c2 = st.columns(2)
    with c1:
        if planning.actions:
            st.plotly_chart(fig_actions_status(planning), use_container_width=True, key="dash_actions_status")
        else:
            st.info("Nenhum plano de ação cadastrado.")
    with c2:
        if planning.swot:
            st.plotly_chart(fig_swot_quadrant(planning.swot), use_container_width=True, key="dash_swot_quad")
        else:
            st.info("Nenhum item SWOT cadastrado.")

    # Planos atrasados
    if n_atrasados > 0:
        st.markdown("---")
        st.error(f"⚠️ **{n_atrasados} plano(s) atrasado(s)** — atenção imediata necessária!")
        df_atrasados = pd.DataFrame([
            {"Título": a.titulo, "Área": a.area, "Responsável": a.responsavel,
             "Vencimento": a.data_vencimento,
             "Dias de atraso": max(0, (today - _safe_date(a.data_vencimento)).days)
                               if _safe_date(a.data_vencimento) else 0}
            for a in planning.actions
            if a.status != "Concluído" and _safe_date(a.data_vencimento)
            and _safe_date(a.data_vencimento) < today
        ]).sort_values("Dias de atraso", ascending=False)
        st.dataframe(df_atrasados, use_container_width=True, hide_index=True)


# ══════════════ TAB 1: SÓCIOS / GESTORES ══════════════
with tabs[1]:
    st.markdown('<div class="section-title">👥 Sócios/Gestores</div>', unsafe_allow_html=True)

    c1, c2 = st.columns([3, 2])
    with c1:
        st.caption("**Adicionar novo sócio/Gestor**")
        n1, n2 = st.columns(2)
        nome = n1.text_input("Nome completo", key="p_nome")
        cargo = n2.text_input("Cargo", key="p_cargo")
        n3, n4 = st.columns(2)
        email = n3.text_input("E-mail", key="p_email")
        tel = n4.text_input("Telefone", key="p_tel")
        obs = st.text_area("Observações", height=70, key="p_obs")
        if st.button("➕ Adicionar", key="p_add", type="primary"):
            if nome.strip():
                planning.partners.append(Partner(nome, cargo, email, tel, obs))
                save_planning(planning)
                st.success("Sócio adicionado!")
                st.rerun()
            else:
                st.warning("Informe o nome.")

    with c2:
        if planning.partners:
            sel = st.selectbox("Excluir sócio/Gestor", ["— selecionar —"] + [f"{i}: {p.nome}" for i, p in enumerate(planning.partners)], key="p_sel")
            if sel != "— selecionar —" and st.button("🗑️ Excluir", key="p_del"):
                idx = int(sel.split(":")[0])
                planning.partners.pop(idx)
                save_planning(planning)
                st.success("Excluído.")
                st.rerun()

    if planning.partners:
        st.markdown("**Tabela de sócios/gestores (editável)**")
        df_p = pd.DataFrame([asdict(p) for p in planning.partners])
        df_p.columns = ["Nome","Cargo","E-mail","Telefone","Observações"]
        edited_p = try_data_editor(df_p, key="partners_editor", height=250,
                                   use_container_width=True, num_rows="dynamic")
        if edited_p is not None and st.button("💾 Salvar alterações (Sócios/Gestores)", key="p_save"):
            planning.partners = []
            for _, r in edited_p.iterrows():
                if str(r.get("Nome","")).strip():
                    planning.partners.append(Partner(
                        str(r.get("Nome","")), str(r.get("Cargo","")),
                        str(r.get("E-mail","")), str(r.get("Telefone","")),
                        str(r.get("Observações",""))
                    ))
            save_planning(planning)
            st.success("Sócio/Gestor salvo!")
            st.rerun()
    else:
        st.info("Nenhum sócio/Gestor cadastrado ainda.")


# ══════════════ TAB 2: ESTRATÉGIA ══════════════
with tabs[2]:
    st.markdown('<div class="section-title">🧭 Informações Estratégicas</div>', unsafe_allow_html=True)
    st.caption("O 'norte' da empresa. Alimenta relatórios e dá coerência a OKRs e planos de ação.")
    s = planning.strategic

    colA, colB = st.columns(2)
    with colA:
        s.visao           = st.text_area("🎯 Visão (onde queremos chegar)", value=s.visao, height=100, key="s_visao")
        s.missao          = st.text_area("🚀 Missão (por que existimos)", value=s.missao, height=100, key="s_missao")
        s.proposta_valor  = st.text_area("💎 Proposta de valor", value=s.proposta_valor, height=100, key="s_pv")
        s.publico_alvo    = st.text_area("👤 Público-alvo / ICP", value=s.publico_alvo, height=100, key="s_pub")
    with colB:
        s.valores         = st.text_area("⭐ Valores (comportamentos inegociáveis)", value=s.valores, height=100, key="s_val")
        s.posicionamento  = st.text_area("🏆 Posicionamento", value=s.posicionamento, height=100, key="s_pos")
        s.diferenciais    = st.text_area("⚡ Diferenciais competitivos", value=s.diferenciais, height=100, key="s_dif")
        s.pilares         = st.text_area("🏛️ Pilares estratégicos (3–6)", value=s.pilares, height=100, key="s_pil")

    s.objetivos_estrategicos = st.text_area("📋 Objetivos estratégicos (alto nível)", value=s.objetivos_estrategicos, height=120, key="s_obj")
    s.notas = st.text_area("📝 Notas / hipóteses / restrições", value=s.notas, height=80, key="s_not")

    if st.button("💾 Salvar Estratégia", key="s_save", type="primary"):
        planning.strategic = s
        save_planning(planning)
        st.success("Estratégia salva!")

    with st.expander("💡 Modelo rápido para preenchimento"):
        st.markdown("""
- **Visão**: verbo + impacto + prazo. Ex.: *"Ser referência regional em engenharia elétrica industrial até 2029"*
- **Missão**: público + entrega + diferencial. Ex.: *"Ajudar indústrias a reduzir custos com projetos elétricos seguros e inovadores"*
- **Pilares**: 4–6 temas. Ex.: Crescimento Comercial, Excelência Técnica, Inovação/BIM, Pessoas, Sustentabilidade
- **Objetivos**: conecte diretamente com seus OKRs: cada objetivo deve ter pelo menos 1 OKR mensurável
        """)


# ══════════════ TAB 3: ÁREAS ══════════════
with tabs[3]:
    st.markdown('<div class="section-title">🏢 Áreas e Responsáveis</div>', unsafe_allow_html=True)

    _resp_options = [p.nome for p in planning.partners] if planning.partners else []

    c1, c2 = st.columns([3, 2])
    with c1:
        n1, n2 = st.columns(2)
        area_n = n1.text_input("Área", key="ar_area")
        if _resp_options:
            resp = n2.selectbox("Responsável", options=_resp_options + ["(outro)"], key="ar_resp")
            if resp == "(outro)":
                resp = n2.text_input("Nome do responsável", key="ar_resp_other")
        else:
            resp = n2.text_input("Responsável", key="ar_resp_txt")
        n3, n4 = st.columns(2)
        a_email = n3.text_input("E-mail", key="ar_email")
        a_obs   = n4.text_input("Observações", key="ar_obs")
        if st.button("➕ Adicionar Área", key="ar_add", type="primary"):
            if area_n.strip():
                resp_val = resp if _resp_options and resp != "(outro)" else (resp if _resp_options else st.session_state.get("ar_resp_txt",""))
                planning.areas.append(AreaResponsavel(area_n, resp_val, a_email, a_obs))
                save_planning(planning)
                st.success("Área adicionada!")
                st.rerun()
            else:
                st.warning("Informe a área.")

    with c2:
        if planning.areas:
            sel_a = st.selectbox("Excluir área", ["— selecionar —"] + [f"{i}: {a.area}" for i, a in enumerate(planning.areas)], key="ar_sel")
            if sel_a != "— selecionar —" and st.button("🗑️ Excluir Área", key="ar_del"):
                idx = int(sel_a.split(":")[0])
                planning.areas.pop(idx)
                save_planning(planning)
                st.success("Excluído.")
                st.rerun()

    if planning.areas:
        st.markdown("**Tabela de áreas (editável)**")
        df_a = pd.DataFrame([asdict(a) for a in planning.areas])
        df_a.columns = ["Área","Responsável","E-mail","Observações"]
        _resp_col_opts = [p.nome for p in planning.partners] if planning.partners else None
        _area_col_config = {
            "Área": st.column_config.TextColumn("Área", width="medium"),
            "E-mail": st.column_config.TextColumn("E-mail", width="medium"),
            "Observações": st.column_config.TextColumn("Observações", width="medium"),
        }
        if _resp_col_opts:
            _area_col_config["Responsável"] = st.column_config.SelectboxColumn(
                "Responsável", options=_resp_col_opts, width="medium")
        edited_a = try_data_editor(df_a, key="areas_editor", height=250,
                                   column_config=_area_col_config,
                                   use_container_width=True, num_rows="dynamic")
        if edited_a is not None and st.button("💾 Salvar Áreas", key="ar_save"):
            planning.areas = []
            for _, r in edited_a.iterrows():
                if str(r.get("Área","")).strip():
                    planning.areas.append(AreaResponsavel(
                        str(r.get("Área","")), str(r.get("Responsável","")),
                        str(r.get("E-mail","")), str(r.get("Observações",""))
                    ))
            save_planning(planning)
            st.success("Áreas salvas!")
            st.rerun()
    else:
        st.info("Nenhuma área cadastrada.")


# ══════════════ TAB 4: SWOT ══════════════
with tabs[4]:
    st.markdown('<div class="section-title">⚖️ Análise SWOT</div>', unsafe_allow_html=True)

    # Gráfico 4 quadrantes
    if planning.swot:
        st.plotly_chart(fig_swot_quadrant(planning.swot), use_container_width=True, key="swot_tab_quad")
    else:
        st.info("Adicione itens SWOT abaixo para ver a matriz visual.")

    st.markdown("**📝 Editar itens SWOT (estilo Excel)**")
    st.caption("Edite diretamente nas células. Use a coluna **Excluir** para remover linhas. Adicione linhas novas na parte inferior.")

    df_swot = pd.DataFrame([asdict(s) for s in planning.swot]) if planning.swot else pd.DataFrame(columns=["tipo","descricao","prioridade"])
    df_swot.columns = ["Tipo","Descrição","Prioridade"] if not df_swot.empty else ["Tipo","Descrição","Prioridade"]
    if "Excluir" not in df_swot.columns:
        df_swot["Excluir"] = False

    edited_swot = try_data_editor(
        df_swot, key="swot_editor", height=360, use_container_width=True, num_rows="dynamic",
        column_config={
            "Tipo": st.column_config.SelectboxColumn("Tipo",
                options=["Força","Fraqueza","Oportunidade","Ameaça"], required=True, width="small"),
            "Prioridade": st.column_config.SelectboxColumn("Prioridade",
                options=["Alta","Média","Baixa"], required=True, width="small"),
            "Descrição": st.column_config.TextColumn("Descrição", width="large"),
            "Excluir": st.column_config.CheckboxColumn("Excluir", width="small"),
        }
    )

    col_s1, col_s2 = st.columns([2, 3])
    with col_s1:
        if st.button("💾 Salvar SWOT", key="swot_save", type="primary"):
            if edited_swot is not None:
                new_items = []
                for _, r in edited_swot.iterrows():
                    if bool(r.get("Excluir", False)): continue
                    desc = str(r.get("Descrição","")).strip()
                    if not desc: continue
                    new_items.append(SWOTItem(
                        tipo=str(r.get("Tipo","Força")),
                        descricao=desc,
                        prioridade=str(r.get("Prioridade","Média"))
                    ))
                planning.swot = new_items
                save_planning(planning)
                st.success("SWOT salva!")
                st.rerun()

    with col_s2:
        if planning.swot:
            counts = {}
            for item in planning.swot:
                counts[item.tipo] = counts.get(item.tipo, 0) + 1
            st.markdown(" ".join([f"**{t}**: {c}" for t, c in counts.items()]))


# ══════════════ TAB 5: OKRs ══════════════
with tabs[5]:
    st.markdown('<div class="section-title">📈 OKRs — Objetivos e Resultados-Chave (36 meses)</div>', unsafe_allow_html=True)
    st.caption("1) Cadastre OKRs na tabela de metadados. 2) Preencha Previsto e Realizado. 3) Analise gráficos e indicadores.")

    # ---- Cadastro de OKRs ----
    with st.expander("➕ **Cadastro e Gestão de OKRs** (clique para expandir)", expanded=True):
        df_meta = _okr_meta_df(planning)
        unidade_opts = ["R$", "%", "Inteiro", "horas", "projetos", "clientes", "NPS"]

        st.caption("Edite diretamente. Adicione novas linhas pelo botão '+'. Marque **Excluir** para remover.")
        edited_meta = try_data_editor(
            df_meta, key="okr_meta_editor", height=280, use_container_width=True,
            disabled=["okr_id"],
            column_config={
                "okr_id": st.column_config.NumberColumn("ID", width="small"),
                "OKR": st.column_config.TextColumn("Nome da OKR", width="large"),
                "Área": st.column_config.TextColumn("Área", width="medium"),
                "Unidade": st.column_config.SelectboxColumn("Unidade", options=unidade_opts, required=True, width="small"),
                "Descrição": st.column_config.TextColumn("Descrição", width="large"),
                "Início": st.column_config.DateColumn("Início", format="MM/YYYY", width="small"),
                "Excluir": st.column_config.CheckboxColumn("Excluir", width="small"),
            }
        )
        c_m1, c_m2 = st.columns([1, 3])
        with c_m1:
            if st.button("💾 Aplicar OKRs", key="okr_meta_apply", type="primary"):
                if edited_meta is not None:
                    _sync_okrs_from_meta(planning, edited_meta)
                    save_planning(planning)
                    st.success("OKRs atualizadas!")
                    st.rerun()
        with c_m2:
            st.info("Dica: Crie uma nova linha em branco para adicionar uma OKR. Salve após editar.")

    if not planning.okrs:
        st.warning("⚠️ Cadastre ao menos 1 OKR para liberar as tabelas de Previsto/Realizado e os gráficos.")
        st.stop()

    okr_names = [o.nome for o in planning.okrs]

    # ---- Previsto ----
    with st.expander("📋 **Planejado (Previsto) — 36 meses**", expanded=False):
        st.caption("Colunas M01..M36 = meses a partir do Início de cada OKR. Edite os valores diretamente.")
        df_prev = _okr_wide_df(planning, "previsto")
        month_cols_prev = {f"M{k+1:02d}": st.column_config.NumberColumn(f"M{k+1:02d}", format="%.2f", step=0.01)
                           for k in range(36)}
        edited_prev = try_data_editor(
            df_prev, key="okr_prev_editor", height=320, use_container_width=True,
            num_rows="dynamic",
            disabled=["okr_id","Unidade"],
            column_config={
                "OKR": st.column_config.SelectboxColumn("OKR", options=okr_names, required=True),
                "Unidade": st.column_config.TextColumn("Unidade", width="small"),
                **month_cols_prev
            }
        )
        if st.button("💾 Salvar Planejado", key="okr_prev_save", type="primary"):
            if edited_prev is not None:
                _apply_wide_to_okrs(planning, edited_prev, "previsto")
                save_planning(planning)
                st.success("Planejado salvo!")
                st.rerun()

    # ---- Realizado ----
    with st.expander("📊 **Realizado — 36 meses**", expanded=False):
        st.caption("Preencha conforme o realizado mês a mês.")
        df_real = _okr_wide_df(planning, "realizado")
        month_cols_real = {f"M{k+1:02d}": st.column_config.NumberColumn(f"M{k+1:02d}", format="%.2f", step=0.01)
                           for k in range(36)}
        edited_real = try_data_editor(
            df_real, key="okr_real_editor", height=320, use_container_width=True,
            num_rows="dynamic",
            disabled=["okr_id","Unidade"],
            column_config={
                "OKR": st.column_config.SelectboxColumn("OKR", options=okr_names, required=True),
                "Unidade": st.column_config.TextColumn("Unidade", width="small"),
                **month_cols_real
            }
        )
        if st.button("💾 Salvar Realizado", key="okr_real_save", type="primary"):
            if edited_real is not None:
                _apply_wide_to_okrs(planning, edited_real, "realizado")
                save_planning(planning)
                st.success("Realizado salvo!")
                st.rerun()

    # ---- Análise por OKR ----
    st.markdown("---")
    st.markdown("### 🔍 Análise Detalhada por OKR")

    sel_okr = st.selectbox("Selecionar OKR para análise", options=okr_names, key="okr_sel_analysis")
    okr_obj = next((o for o in planning.okrs if o.nome == sel_okr), None)

    if okr_obj:
        labels = _month_labels_for_okr(okr_obj)
        prev = [float(okr_obj.meses[k].previsto) if k < len(okr_obj.meses) else 0.0 for k in range(36)]
        real = [float(okr_obj.meses[k].realizado) if k < len(okr_obj.meses) else 0.0 for k in range(36)]

        # KPIs da OKR
        tp_okr = sum(prev); tr_okr = sum(real)
        pct_okr = (tr_okr / tp_okr * 100) if tp_okr > 0 else 0
        ks = st.columns(4)
        ks[0].metric("Total Planejado", f"{tp_okr:,.2f}", help=f"Unidade: {okr_obj.unidade}")
        ks[1].metric("Total Realizado", f"{tr_okr:,.2f}", help=f"Unidade: {okr_obj.unidade}")
        ks[2].metric("Diferença", f"{tr_okr - tp_okr:+,.2f}")
        ks[3].metric("% Realização", f"{pct_okr:.1f}%",
                     delta=f"{pct_okr - 100:.1f}%",
                     delta_color="normal" if pct_okr >= 100 else "inverse")

        # Gauge
        cg, _ = st.columns([1, 3])
        with cg:
            st.plotly_chart(fig_okr_gauge(okr_obj), use_container_width=True, key="okr_tab_gauge")

        # Gráfico mensal
        st.plotly_chart(fig_okr_monthly(okr_obj), use_container_width=True, key="okr_tab_monthly")

        # Acumulado
        st.plotly_chart(fig_okr_cumulative(okr_obj), use_container_width=True, key="okr_tab_cumulative")

        # Tabela de comparação
        st.markdown("**📋 Tabela Comparativa Mensal**")
        diff = [r - p for r, p in zip(real, prev)]
        status_list = ["✅ Acima" if d > 0 else ("⚠️ Abaixo" if d < 0 else "➖ Meta") for d in diff]
        df_cmp = pd.DataFrame({
            "Mês": labels, "Planejado": prev, "Realizado": real,
            "Diferença": diff, "Status": status_list
        })

        def style_diff(val):
            if isinstance(val, (int, float)):
                color = "#059669" if val >= 0 else "#DC2626"
                return f"color: {color}; font-weight: 600"
            return ""

        st.dataframe(
            df_cmp.style.applymap(style_diff, subset=["Diferença"]),
            use_container_width=True, height=400, hide_index=True
        )

        # Legenda meses
        with st.expander("📅 Legenda das colunas M01..M36"):
            st.dataframe(pd.DataFrame({"Coluna": [f"M{k+1:02d}" for k in range(36)], "Mês": labels}),
                         use_container_width=True, height=300, hide_index=True)


# ══════════════ TAB 6: PLANOS DE AÇÃO ══════════════
with tabs[6]:
    st.markdown('<div class="section-title">✅ Planos de Ação</div>', unsafe_allow_html=True)

    # ── Formulário: adicionar novo plano ──
    with st.expander("➕ **Adicionar novo Plano de Ação**", expanded=False):
        area_opts  = [a.area for a in planning.areas]  if planning.areas   else []
        resp_opts  = [p.nome for p in planning.partners] if planning.partners else []
        okr_opts   = [o.nome for o in planning.okrs]   if planning.okrs    else []

        fa1, fa2, fa3 = st.columns(3)
        novo_titulo = fa1.text_input("Título *", key="na_titulo")
        novo_okr    = fa2.selectbox("OKR vinculada", options=["(nenhuma)"] + okr_opts, key="na_okr") if okr_opts \
                      else fa2.text_input("OKR vinculada", key="na_okr_txt")
        novo_area   = fa3.selectbox("Área", options=area_opts + ["(outra)"], key="na_area") if area_opts \
                      else fa3.text_input("Área", key="na_area_txt")

        fb1, fb2, fb3 = st.columns(3)
        novo_resp   = fb1.selectbox("Responsável", options=resp_opts + ["(outro)"], key="na_resp") if resp_opts \
                      else fb1.text_input("Responsável", key="na_resp_txt")
        novo_inicio = fb2.date_input("Data Início", value=date.today(), key="na_inicio")
        novo_venc   = fb3.date_input("Data Vencimento", value=date.today(), key="na_venc")

        fc1, fc2, fc3 = st.columns(3)
        novo_status = fc1.selectbox("Status", ["Pendente","Em andamento","Concluído"], key="na_status")
        novo_desc   = fc2.text_input("Descrição", key="na_desc")
        novo_como   = fc3.text_input("Como Fazer", key="na_como")

        novo_obs = st.text_input("Observações", key="na_obs")

        if st.button("➕ Adicionar Plano", key="na_add", type="primary"):
            if not novo_titulo.strip():
                st.warning("Informe o Título do plano.")
            else:
                area_val = novo_area if area_opts and novo_area != "(outra)" else st.session_state.get("na_area_txt","")
                resp_val = novo_resp if resp_opts and novo_resp != "(outro)" else st.session_state.get("na_resp_txt","")
                okr_val  = novo_okr if okr_opts and novo_okr != "(nenhuma)" else st.session_state.get("na_okr_txt","")
                planning.actions.append(PlanoAcao(
                    titulo=novo_titulo.strip(),
                    area=area_val,
                    responsavel=resp_val,
                    descricao=novo_desc.strip(),
                    data_inicio=novo_inicio.strftime("%Y-%m-%d"),
                    data_vencimento=novo_venc.strftime("%Y-%m-%d"),
                    status=novo_status,
                    observacoes=novo_obs.strip(),
                    okr=okr_val,
                    como_fazer=novo_como.strip(),
                ))
                save_planning(planning)
                st.success(f"Plano **{novo_titulo}** adicionado!")
                st.rerun()

    # ── Legenda semáforo ──
    st.markdown("🟢 Concluído &nbsp;&nbsp; 🟡 Vence em ≤2 dias &nbsp;&nbsp; 🔴 Atrasado &nbsp;&nbsp; ⚪ No prazo")

    # ── Tabela editável ──
    st.markdown("**📋 Tabela de Planos (editável — clique na célula para alterar)**")
    st.caption("Edite diretamente nas células. Marque **Excluir** para remover. Clique em 💾 Salvar ao terminar.")

    df_act = _actions_df(planning).drop(columns=["Status Efetivo"], errors="ignore")

    # Converter colunas de data de string para datetime.date (obrigatório para DateColumn)
    for _dcol in ["Início", "Vencimento"]:
        if _dcol in df_act.columns:
            df_act[_dcol] = pd.to_datetime(df_act[_dcol], errors="coerce").dt.date

    # Montar column_config com dropdowns dinâmicos
    _act_area_opts = [a.area for a in planning.areas] if planning.areas else None
    _act_okr_opts  = [o.nome for o in planning.okrs] if planning.okrs else None
    _act_resp_opts = [p.nome for p in planning.partners] if planning.partners else None

    _act_col_config = {
        "":            st.column_config.TextColumn("", width="small", disabled=True),
        "Título":      st.column_config.TextColumn("Título", width="large"),
        "Descrição":   st.column_config.TextColumn("Descrição", width="large"),
        "Como Fazer":  st.column_config.TextColumn("Como Fazer", width="large"),
        "Início":      st.column_config.DateColumn("Início", format="DD/MM/YYYY"),
        "Vencimento":  st.column_config.DateColumn("Vencimento", format="DD/MM/YYYY"),
        "Status":      st.column_config.SelectboxColumn("Status",
                           options=["Pendente","Em andamento","Concluído"],
                           required=True, width="small"),
        "Observações": st.column_config.TextColumn("Observações", width="medium"),
        "Excluir":     st.column_config.CheckboxColumn("Excluir", width="small"),
    }
    if _act_area_opts:
        _act_col_config["Área"] = st.column_config.SelectboxColumn(
            "Área", options=_act_area_opts, width="medium")
    if _act_okr_opts:
        _act_col_config["OKR"] = st.column_config.SelectboxColumn(
            "OKR", options=_act_okr_opts, width="medium")
    if _act_resp_opts:
        _act_col_config["Responsável"] = st.column_config.SelectboxColumn(
            "Responsável", options=_act_resp_opts, width="medium")

    edited_act = st.data_editor(
        df_act,
        key="actions_editor",
        height=420,
        use_container_width=True,
        num_rows="dynamic",
        column_config=_act_col_config,
        hide_index=True,
    )

    c_a1, c_a2 = st.columns([1, 4])
    with c_a1:
        if st.button("💾 Salvar alterações", key="actions_save", type="primary"):
            if edited_act is not None:
                _sync_actions(planning, edited_act)
                save_planning(planning)
                st.success("Planos salvos!")
                st.rerun()
    with c_a2:
        st.caption("💡 Use o formulário acima para adicionar novos planos. Edite campos e clique em Salvar.")

    # ---- Analytics ----
    st.markdown("---")
    st.markdown("### 📊 Painel de Acompanhamento")

    if planning.actions:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total", len(planning.actions))
        m2.metric("Concluídos", n_concluidos, f"{n_concluidos/max(len(planning.actions),1)*100:.0f}%")
        m3.metric("Em andamento", n_andamento)
        m4.metric("Atrasados", n_atrasados, delta=f"-{n_atrasados}" if n_atrasados else None,
                  delta_color="inverse" if n_atrasados else "off")

        cv1, cv2 = st.columns(2)
        with cv1:
            st.plotly_chart(fig_actions_status(planning), use_container_width=True, key="act_tab_status")
        with cv2:
            # Atrasos por responsável
            df_a_full = _actions_df(planning)
            df_a_full["dt_venc"] = df_a_full["Vencimento"].apply(_safe_date)
            df_a_full["Atraso"] = df_a_full.apply(
                lambda r: max(0, (today - r["dt_venc"]).days)
                if (r["dt_venc"] and r["Status"] != "Concluído" and r["dt_venc"] < today) else 0, axis=1
            )
            df_atrasados_resp = df_a_full[df_a_full["Atraso"] > 0].groupby("Responsável")["Atraso"].sum().reset_index()
            if not df_atrasados_resp.empty:
                fig_resp = px.bar(df_atrasados_resp, x="Responsável", y="Atraso",
                                  title="Atraso total por Responsável (dias)",
                                  color="Atraso", color_continuous_scale=["#FEF3C7","#DC2626"])
                st.plotly_chart(_fig_layout(fig_resp, height=320), use_container_width=True, key="act_tab_resp")
            else:
                st.success("✅ Nenhum plano atrasado!")

        # Gantt
        st.markdown("**📅 Linha do Tempo (Gantt)**")
        st.plotly_chart(fig_actions_timeline(planning), use_container_width=True, key="act_tab_gantt")
    else:
        st.info("Nenhum plano de ação cadastrado ainda.")


# ══════════════ TAB 7: RELATÓRIOS ══════════════
with tabs[7]:
    st.markdown('<div class="section-title">📄 Relatórios e Direcionamento Estratégico</div>', unsafe_allow_html=True)

    # Sumário estratégico
    s = planning.strategic
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 🎯 Norte Estratégico")
        st.markdown(f"**Visão:** {s.visao or '—'}")
        st.markdown(f"**Missão:** {s.missao or '—'}")
        st.markdown(f"**Pilares:** {s.pilares or '—'}")
    with col2:
        st.markdown("#### 💎 Mercado & Proposta")
        st.markdown(f"**Proposta de valor:** {s.proposta_valor or '—'}")
        st.markdown(f"**Público-alvo:** {s.publico_alvo or '—'}")
        st.markdown(f"**Diferenciais:** {s.diferenciais or '—'}")

    st.markdown("---")

    # Sinais de execução OKRs
    if planning.okrs:
        st.markdown("#### 📈 Saúde das OKRs")
        rows = []
        for o in planning.okrs:
            real_vals = [m.realizado for m in o.meses[:36]]
            prev_vals = [m.previsto  for m in o.meses[:36]]
            filled = sum(1 for v in real_vals if float(v) != 0.0)
            tp = sum(prev_vals); tr = sum(real_vals)
            pct = (tr / tp * 100) if tp > 0 else 0
            semaforo = "🟢" if pct >= 95 else ("🟡" if pct >= 70 else "🔴")
            rows.append({"": semaforo, "OKR": o.nome, "Área": o.area, "Unidade": o.unidade,
                         "% Realização": f"{pct:.1f}%",
                         "Meses preenchidos": f"{filled}/36 ({filled/36*100:.0f}%)"})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=200, hide_index=True)

    # Recomendações automáticas
    st.markdown("---")
    st.markdown("#### 🧠 Recomendações Automáticas")
    recs = []
    if planning.swot:
        threats = [s for s in planning.swot if s.tipo == "Ameaça" and s.prioridade == "Alta"]
        opps    = [s for s in planning.swot if s.tipo == "Oportunidade" and s.prioridade == "Alta"]
        weaknesses = [s for s in planning.swot if s.tipo == "Fraqueza" and s.prioridade == "Alta"]
        if threats:    recs.append(f"🔴 {len(threats)} **Ameaça(s) Alta** — crie planos de mitigação com responsável e prazo claro.")
        if opps:       recs.append(f"🔵 {len(opps)} **Oportunidade(s) Alta** — transforme em 1–2 OKRs por pilar estratégico.")
        if weaknesses: recs.append(f"🟡 {len(weaknesses)} **Fraqueza(s) Alta** — endereçar com planos de ação de curto prazo.")
    if n_atrasados:
        recs.append(f"⚠️ **{n_atrasados} plano(s) atrasado(s)** — priorize replanejamento: escopo, capacidade, nova data.")
    if planning.okrs:
        recs.append("📅 Estabeleça **revisão mensal** do realizado e **revisão trimestral** de OKRs e prioridades.")
        low_fill = [o.nome for o in planning.okrs
                    if sum(1 for m in o.meses if m.realizado != 0) < 3]
        if low_fill:
            recs.append(f"📊 OKR(s) com pouco histórico: **{', '.join(low_fill[:3])}** — preencha o realizado mensalmente.")
    if not recs:
        recs.append("✅ Preencha Visão/Missão, SWOT e OKRs para gerar recomendações automáticas.")

    for r in recs:
        st.markdown(f"- {r}")

    # Exportar relatório
    st.markdown("---")
    st.markdown("#### 📥 Exportar Relatório")
    c_r1, c_r2, c_r3 = st.columns(3)
    with c_r1:
        if st.button("🌐 Gerar Relatório HTML", key="full_html_rep", type="primary"):
            html = build_html_report(planning)
            st.download_button("⬇️ Baixar HTML", data=html.encode("utf-8"),
                               file_name="relatorio_planejamento.html", mime="text/html", key="dl_rep_html")
            st.success("Relatório HTML pronto!")
    with c_r2:
        if st.button("📊 Exportar Excel completo", key="rep_xlsx"):
            xlsx = export_to_excel_bytes(planning)
            st.download_button("⬇️ Baixar Excel", data=xlsx,
                               file_name="planejamento_completo.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key="dl_rep_xlsx")
    with c_r3:
        if st.button("🗜️ Exportar CSVs ZIP", key="rep_zip"):
            z = export_to_csv_zip(planning)
            st.download_button("⬇️ Baixar ZIP", data=z,
                               file_name="planning_csvs.zip", mime="application/zip", key="dl_rep_zip")

    # Preview HTML
    with st.expander("👁️ Preview do Relatório HTML (in-app)"):
        if st.button("Renderizar preview", key="preview_btn"):
            html = build_html_report(planning)
            st.components.v1.html(html, height=900, scrolling=True)

st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
st.markdown("<footer style='text-align:center;color:#94A3B8;font-size:12px;padding:10px'>Produzido por BK Engenharia e Tecnologia</footer>", unsafe_allow_html=True)