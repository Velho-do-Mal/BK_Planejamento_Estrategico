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

# ============================================
# CONFIGURAÇÃO DO BANCO DE DADOS (Neon)
# ============================================
_DB_CONN_FALLBACK = "postgresql://neondb_owner:npg_TiJv0WHSG7pU@ep-jolly-heart-ahj739cl-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

def _get_db_conn_str() -> str:
    """Retorna connection string: secrets.toml > env var > hardcoded fallback."""
    try:
        secret = st.secrets.get("neon", {}).get("connection", "")
        if secret and secret.strip():
            return secret.strip()
    except Exception:
        pass
    env = os.environ.get("NEON_DATABASE_URL", "")
    if env:
        return env
    return _DB_CONN_FALLBACK

# Será resolvido após st estar disponível
DB_CONN_STR = _DB_CONN_FALLBACK  # placeholder — atualizado no boot

# ============================================
# PALETA BK — cores consistentes
# ============================================
BK_BLUE      = "#1565C0"
BK_BLUE_LIGHT= "#42A5F5"
BK_TEAL      = "#00897B"
BK_GREEN     = "#43A047"
BK_ORANGE    = "#FB8C00"
BK_RED       = "#E53935"
BK_PURPLE    = "#7B1FA2"
BK_GRAY      = "#546E7A"
BK_BG        = "#F0F4F8"
BK_CARD      = "#FFFFFF"
BK_DARK      = "#0D1B2A"

SWOT_COLORS = {
    "Força":       "#43A047",
    "Fraqueza":    "#E53935",
    "Oportunidade":"#1565C0",
    "Ameaça":      "#FB8C00",
}
STATUS_COLORS = {
    "Concluído":   BK_GREEN,
    "Em andamento":BK_ORANGE,
    "Pendente":    BK_GRAY,
    "Atrasado":    BK_RED,
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

def export_to_postgres(planning: PlanningData, conn_str: str = "") -> str:
    if not conn_str:
        conn_str = os.environ.get("NEON_DATABASE_URL", "")
        try:
            if hasattr(st, "secrets"):
                neon_secret = st.secrets.get("neon", {}).get("connection")
                if neon_secret:
                    conn_str = neon_secret
        except Exception:
            pass
    if not conn_str:
        return "❌ Connection string vazia. Configure NEON_DATABASE_URL ou st.secrets['neon']['connection']."
    try:
        engine = create_engine(conn_str, future=True)
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

            # Garantir que colunas novas existam em tabelas antigas
            for _col, _def in [("okr", "TEXT DEFAULT ''"), ("como_fazer", "TEXT DEFAULT ''")]:
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
        engine = create_engine(conn_str, future=True)
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

SQLITE_PATH = "bk_planejamento.db"

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

PLOTLY_TEMPLATE = "plotly_white"

def _fig_layout(fig, title="", height=380, xangle=-30):
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color=BK_DARK, family="Segoe UI")),
        template=PLOTLY_TEMPLATE,
        height=height,
        margin=dict(l=30, r=30, t=50, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
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
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
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
                      paper_bgcolor="rgba(0,0,0,0)")
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
        height=480, template="plotly_white",
        margin=dict(l=10, r=10, t=50, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(245,248,252,1)",
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
                      font=dict(family="Segoe UI"), plot_bgcolor="rgba(0,0,0,0)",
                      paper_bgcolor="rgba(0,0,0,0)",
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
/* Fonte e background global */
html, body, [class*="css"] { font-family: 'Segoe UI', sans-serif; }
.stApp { background: #F0F4F8; }

/* Header hero */
.bk-hero {
    background: linear-gradient(135deg, #1565C0 0%, #00897B 100%);
    padding: 24px 32px; border-radius: 12px; margin-bottom: 20px;
    color: white; box-shadow: 0 4px 20px rgba(21,101,192,0.3);
}
.bk-hero h1 { font-size: 24px; font-weight: 700; margin: 0; letter-spacing: -0.5px; }
.bk-hero p { font-size: 13px; opacity: 0.85; margin: 4px 0 0; }

/* KPI cards */
.kpi-card {
    background: white; border-radius: 10px; padding: 16px 20px;
    text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    border-top: 3px solid #1565C0;
}
.kpi-card .val { font-size: 28px; font-weight: 700; color: #1565C0; }
.kpi-card .lbl { font-size: 11px; color: #64748B; margin-top: 2px; text-transform: uppercase; letter-spacing: 0.5px; }

/* Section title */
.section-title {
    font-size: 15px; font-weight: 600; color: #1565C0;
    padding: 6px 0 10px; border-bottom: 2px solid #E3F2FD; margin-bottom: 14px;
}

/* Tab styling */
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] { padding: 8px 18px; border-radius: 8px 8px 0 0; font-size: 13px; font-weight: 500; }

/* Sidebar */
[data-testid="stSidebar"] { background: #0D1B2A; }
[data-testid="stSidebar"] * { color: #CBD5E1 !important; }
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { color: #93C5FD !important; }
[data-testid="stSidebar"] .stButton>button { 
    background: #1565C0; color: white !important; border: none;
    border-radius: 6px; width: 100%; margin-bottom: 4px;
}
[data-testid="stSidebar"] .stButton>button:hover { background: #1976D2; }

/* Botões primários */
.stButton>button[kind="primary"] { background: #1565C0; border: none; }
.stButton>button { border-radius: 6px; font-weight: 500; }

/* Alertas e info */
.stSuccess { background: #D1FAE5; border-color: #059669; }
.stWarning { background: #FEF3C7; border-color: #D97706; }
.stError   { background: #FEE2E2; border-color: #DC2626; }

/* DataEditor */
[data-testid="stDataEditor"] { border-radius: 8px; overflow: hidden; }

/* ── Campos de texto / textarea com borda visível ── */
div[data-baseweb="input"] > div,
div[data-baseweb="textarea"] > div {
    background-color: #FFFFFF !important;
    border: 1.5px solid #90A4AE !important;
    border-radius: 6px !important;
}
div[data-baseweb="input"] > div:focus-within,
div[data-baseweb="textarea"] > div:focus-within {
    border-color: #1565C0 !important;
    box-shadow: 0 0 0 2px rgba(21,101,192,0.15) !important;
}
div[data-baseweb="input"] input,
div[data-baseweb="textarea"] textarea {
    color: #1a202c !important;
    background-color: #FFFFFF !important;
    font-size: 14px !important;
}
/* Label dos campos */
div[data-testid="stTextInput"] label,
div[data-testid="stTextArea"] label,
div[data-testid="stSelectbox"] label,
div[data-testid="stNumberInput"] label {
    color: #1E3A5F !important;
    font-weight: 600 !important;
    font-size: 13px !important;
}
/* Selectbox */
div[data-baseweb="select"] > div:first-child {
    background-color: #FFFFFF !important;
    border: 1.5px solid #90A4AE !important;
    border-radius: 6px !important;
}
/* Password input */
div[data-baseweb="input"][type="password"] > div {
    background-color: #FFFFFF !important;
    border: 1.5px solid #90A4AE !important;
}
/* Expander header */
details summary {
    background: #EBF3FB !important;
    border-radius: 6px;
    padding: 4px 8px;
}
</style>
""", unsafe_allow_html=True)


# ============================================
# LOAD STATE — AGORA COM CARGA DO BANCO
# ============================================

if "planning" not in st.session_state:
    planning = None
    # 1. SQLite local (principal — sempre atualizado pelo save_planning)
    planning = load_from_sqlite()
    # 2. Retrocompatibilidade: se tiver planning.json antigo, migra para SQLite
    if planning is None and os.path.exists("planning.json"):
        try:
            with open("planning.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            planning = PlanningData.from_dict(data)
            save_to_sqlite(planning)  # migra para SQLite
        except Exception:
            planning = None
    # 3. Neon (nuvem — fallback)
    if planning is None:
        planning = load_from_postgres(DB_CONN_STR)
    # 4. Último recurso: dados vazios
    if planning is None:
        planning = PlanningData()
    st.session_state.planning = planning

planning: PlanningData = st.session_state.planning

def save_planning(pl: PlanningData):
    """Salva no session_state, persiste em SQLite local e sincroniza com Neon."""
    st.session_state.planning = pl
    # 1. Persistência local — SQLite (principal)
    msg_sqlite = save_to_sqlite(pl)
    if "❌" in msg_sqlite:
        st.warning(f"⚠️ {msg_sqlite}")
    # 2. Persistência remota — Neon (automático, sem bloqueio)
    msg = export_to_postgres(pl, DB_CONN_STR)
    if msg and "❌" in msg:
        pass  # falha silenciosa — SQLite já salvou


# ============================================
# HEADER
# ============================================

st.markdown("""
<div class="bk-hero">
    <h1>📊 BK Planejamento Estratégico</h1>
    <p>Planejamento Estratégico 36 meses — BK Engenharia e Tecnologia</p>
</div>
""", unsafe_allow_html=True)


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
    st.markdown("## 💾 Status")
    _ts = sqlite_last_updated()
    if _ts:
        st.caption(f"🗄️ SQLite: ✅ ({_ts})")
    else:
        st.caption("🗄️ SQLite: ⚠️ vazio")
    # Teste automático Neon (status apenas)
    try:
        _eng = create_engine(DB_CONN_STR, future=True)
        with _eng.connect() as _c:
            _c.execute(text("SELECT 1"))
        st.caption("☁️ Neon: 🟢 conectado")
    except Exception:
        st.caption("☁️ Neon: 🔴 offline")

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
def kpi_html(val, label, color="#1565C0"):
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
        if st.button("➕ Adicionar Sócio", key="p_add", type="primary"):
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
