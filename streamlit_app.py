# streamlit_app.py
"""
BK_Planejamento_Estrategico - Aplicação completa em Streamlit (final)
- OKR 36 meses (Previsto + Realizado)
- SWOT com editar/excluir
- Planos de ação com editar/excluir
- Relatório HTML (fonte Segoe UI / Arial fallback), tabelas com bordas, gráficos colunas + linha de tendência
- Exportações: JSON / ZIP (CSV) / Excel / Neon(Postgres) com UPSERT
- Compatibilidade com/sem st.data_editor (editor manual 6x6)
"""

import base64
import io
import json
import os
import zipfile
import inspect
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd
import plotly.graph_objs as go
import streamlit as st
from sqlalchemy import create_engine, text

# -------------------------
# MODELOS DE DADOS
# -------------------------

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
    inicio_ano: int = date.today().year
    inicio_mes: int = date.today().month
    meses: List[OKRMonthData] = field(default_factory=list)

    def __post_init__(self):
        # 36 meses = 3 anos
        if not self.meses:
            ano = self.inicio_ano
            mes = self.inicio_mes
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
    data_inicio: str  # "YYYY-MM-DD"
    data_vencimento: str  # "YYYY-MM-DD"
    status: str = "Pendente"
    observacoes: str = ""


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
                {
                    **{k: v for k, v in asdict(o).items() if k != "meses"},
                    "meses": [asdict(m) for m in o.meses],
                }
                for o in self.okrs
            ],
            "actions": [asdict(ac) for ac in self.actions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanningData":
        pd_obj = cls()
        try:
            pd_obj.strategic = StrategicInfo(**data.get("strategic", {}) )
        except Exception:
            pd_obj.strategic = StrategicInfo()
        for p in data.get("partners", []):
            pd_obj.partners.append(Partner(**p))
        for a in data.get("areas", []):
            pd_obj.areas.append(AreaResponsavel(**a))
        for s in data.get("swot", []):
            pd_obj.swot.append(SWOTItem(**s))
        for o in data.get("okrs", []):
            meses = [OKRMonthData(**m) for m in o.get("meses", [])]
            o_copy = {k: v for k, v in o.items() if k != "meses"}
            okr = OKR(**o_copy)
            if meses:
                okr.meses = meses
            # Normaliza para 36 meses a partir do início (preserva o que existir)
            if len(okr.meses) != 36:
                existing = {(int(m.ano), int(m.mes)): m for m in okr.meses}
                okr.meses = []
                ano = int(okr.inicio_ano)
                mes = int(okr.inicio_mes)
                for _ in range(36):
                    key = (ano, mes)
                    if key in existing:
                        mm = existing[key]
                        okr.meses.append(OKRMonthData(ano=ano, mes=mes, previsto=float(mm.previsto), realizado=float(mm.realizado)))
                    else:
                        okr.meses.append(OKRMonthData(ano=ano, mes=mes))
                    mes += 1
                    if mes > 12:
                        mes = 1
                        ano += 1
            pd_obj.okrs.append(okr)
        for ac in data.get("actions", []):
            try:
                if "data_inicio" not in ac:
                    ac = {**ac, "data_inicio": ac.get("data_vencimento", date.today().strftime("%Y-%m-%d"))}
                pd_obj.actions.append(PlanoAcao(**ac))
            except Exception:
                # fallback minimal
                pd_obj.actions.append(PlanoAcao(
                    titulo=ac.get("titulo",""),
                    area=ac.get("area",""),
                    responsavel=ac.get("responsavel",""),
                    descricao=ac.get("descricao",""),
                    data_inicio=ac.get("data_inicio", date.today().strftime("%Y-%m-%d")),
                    data_vencimento=ac.get("data_vencimento", date.today().strftime("%Y-%m-%d")),
                    status=ac.get("status","Pendente"),
                    observacoes=ac.get("observacoes","")
                ))
        return pd_obj

# -------------------------
# UTILITÁRIOS DE DADOS/EXPORT
# -------------------------

def okr_to_dataframe(okr: OKR) -> pd.DataFrame:
    rows = []
    for i, m in enumerate(okr.meses, start=1):
        rows.append({
            "m_index": i,
            "ano": m.ano,
            "mes": m.mes,
            "previsto": m.previsto,
            "realizado": m.realizado
        })
    return pd.DataFrame(rows)


def planning_to_dataframes(planning: PlanningData) -> Dict[str, pd.DataFrame]:
    df_partners = pd.DataFrame([asdict(p) for p in planning.partners]) if planning.partners else pd.DataFrame(columns=["nome","cargo","email","telefone","observacoes"])
    df_areas = pd.DataFrame([asdict(a) for a in planning.areas]) if planning.areas else pd.DataFrame(columns=["area","responsavel","email","observacoes"])
    df_swot = pd.DataFrame([asdict(s) for s in planning.swot]) if planning.swot else pd.DataFrame(columns=["tipo","descricao","prioridade"])
    okr_rows = []
    okrmes_rows = []
    for idx, o in enumerate(planning.okrs, start=1):
        okr_rows.append({
            "okr_id": idx,
            "nome": o.nome,
            "area": o.area,
            "unidade": o.unidade,
            "descricao": o.descricao,
            "inicio_ano": o.inicio_ano,
            "inicio_mes": o.inicio_mes
        })
        for i, m in enumerate(o.meses, start=1):
            okrmes_rows.append({
                "okr_id": idx,
                "idx_mes": i,
                "ano": m.ano,
                "mes": m.mes,
                "previsto": m.previsto,
                "realizado": m.realizado
            })
    df_okr = pd.DataFrame(okr_rows) if okr_rows else pd.DataFrame(columns=["okr_id","nome","area","unidade","descricao","inicio_ano","inicio_mes"])
    df_okr_mes = pd.DataFrame(okrmes_rows) if okrmes_rows else pd.DataFrame(columns=["okr_id","idx_mes","ano","mes","previsto","realizado"])
    df_actions = pd.DataFrame([asdict(a) for a in planning.actions]) if planning.actions else pd.DataFrame(columns=["titulo","area","responsavel","descricao","data_inicio","data_vencimento","status","observacoes"])
    return {
        "partners": df_partners,
        "areas": df_areas,
        "swot": df_swot,
        "okrs": df_okr,
        "okr_mes": df_okr_mes,
        "actions": df_actions
    }


def export_to_csv_zip(planning: PlanningData) -> bytes:
    dfs = planning_to_dataframes(planning)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, df in dfs.items():
            df_out = df.copy()
            for c in df_out.columns:
                if pd.api.types.is_datetime64_any_dtype(df_out[c]):
                    df_out[c] = df_out[c].dt.strftime("%Y-%m-%d")
            csv_bytes = df_out.to_csv(index=False).encode("utf-8")
            zf.writestr(f"{name}.csv", csv_bytes)
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
    """
    Exporta dados para Neon/Postgres usando UPSERT (ON CONFLICT).
    Usa conn_str, senão NEON_DATABASE_URL env var ou st.secrets['neon']['connection'].
    """
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
        return "Connection string vazia. Configure NEON_DATABASE_URL ou st.secrets['neon']['connection']."

    try:
        engine = create_engine(conn_str, future=True)
    except Exception as e:
        return f"Erro ao criar engine: {e}"

    # Create tables with UNIQUE constraints so ON CONFLICT works
    ddl_statements = [
        """
        CREATE TABLE IF NOT EXISTS partners (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            cargo TEXT,
            email TEXT,
            telefone TEXT,
            observacoes TEXT,
            UNIQUE (nome, email)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS areas (
            id SERIAL PRIMARY KEY,
            area TEXT NOT NULL,
            responsavel TEXT,
            email TEXT,
            observacoes TEXT,
            UNIQUE (area)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS swot (
            id SERIAL PRIMARY KEY,
            tipo TEXT NOT NULL,
            descricao TEXT NOT NULL,
            prioridade TEXT NOT NULL,
            UNIQUE (tipo, descricao)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS okr (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL,
            area TEXT,
            unidade TEXT,
            descricao TEXT,
            inicio_ano INTEGER,
            inicio_mes INTEGER,
            UNIQUE (nome)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS okr_mes (
            id SERIAL PRIMARY KEY,
            okr_id INTEGER NOT NULL REFERENCES okr(id) ON DELETE CASCADE,
            idx_mes INTEGER NOT NULL,
            ano INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            previsto DOUBLE PRECISION NOT NULL,
            realizado DOUBLE PRECISION NOT NULL,
            UNIQUE (okr_id, idx_mes)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS actions (
            id SERIAL PRIMARY KEY,
            titulo TEXT NOT NULL,
            area TEXT,
            responsavel TEXT,
            descricao TEXT,
            data_inicio DATE,
            data_vencimento DATE,
            status TEXT NOT NULL,
            observacoes TEXT,
            UNIQUE (titulo, data_vencimento)
        );
        """
    ]

    try:
        with engine.begin() as conn:
            for ddl in ddl_statements:
                conn.execute(text(ddl))

            # UPSERT partners
            for p in planning.partners:
                conn.execute(
                    text("""
                    INSERT INTO partners (nome,cargo,email,telefone,observacoes)
                    VALUES (:nome,:cargo,:email,:telefone,:observacoes)
                    ON CONFLICT (nome, email) DO UPDATE SET
                      cargo = EXCLUDED.cargo,
                      telefone = EXCLUDED.telefone,
                      observacoes = EXCLUDED.observacoes
                    """),
                    {"nome": p.nome, "cargo": p.cargo, "email": p.email, "telefone": p.telefone, "observacoes": p.observacoes}
                )

            # UPSERT areas
            for a in planning.areas:
                conn.execute(
                    text("""
                    INSERT INTO areas (area,responsavel,email,observacoes)
                    VALUES (:area,:responsavel,:email,:observacoes)
                    ON CONFLICT (area) DO UPDATE SET
                      responsavel = EXCLUDED.responsavel,
                      email = EXCLUDED.email,
                      observacoes = EXCLUDED.observacoes
                    """),
                    {"area": a.area, "responsavel": a.responsavel, "email": a.email, "observacoes": a.observacoes}
                )

            # UPSERT swot
            for s in planning.swot:
                conn.execute(
                    text("""
                    INSERT INTO swot (tipo,descricao,prioridade)
                    VALUES (:tipo,:descricao,:prioridade)
                    ON CONFLICT (tipo, descricao) DO UPDATE SET
                      prioridade = EXCLUDED.prioridade
                    """),
                    {"tipo": s.tipo, "descricao": s.descricao, "prioridade": s.prioridade}
                )

            # UPSERT okr and okr_mes
            for o in planning.okrs:
                res = conn.execute(
                    text("""
                    INSERT INTO okr (nome,area,unidade,descricao,inicio_ano,inicio_mes)
                    VALUES (:nome,:area,:unidade,:descricao,:inicio_ano,:inicio_mes)
                    ON CONFLICT (nome) DO UPDATE SET
                      area = EXCLUDED.area,
                      unidade = EXCLUDED.unidade,
                      descricao = EXCLUDED.descricao,
                      inicio_ano = EXCLUDED.inicio_ano,
                      inicio_mes = EXCLUDED.inicio_mes
                    RETURNING id
                    """),
                    {"nome": o.nome, "area": o.area, "unidade": o.unidade, "descricao": o.descricao, "inicio_ano": o.inicio_ano, "inicio_mes": o.inicio_mes}
                )
                okr_id_row = res.fetchone()
                if okr_id_row:
                    okr_id = okr_id_row[0]
                else:
                    r2 = conn.execute(text("SELECT id FROM okr WHERE nome = :nome"), {"nome": o.nome})
                    row2 = r2.fetchone()
                    okr_id = row2[0] if row2 else None

                if okr_id:
                    for idx, m in enumerate(o.meses, start=1):
                        conn.execute(
                            text("""
                            INSERT INTO okr_mes (okr_id, idx_mes, ano, mes, previsto, realizado)
                            VALUES (:okr_id, :idx_mes, :ano, :mes, :previsto, :realizado)
                            ON CONFLICT (okr_id, idx_mes) DO UPDATE SET
                              ano = EXCLUDED.ano,
                              mes = EXCLUDED.mes,
                              previsto = EXCLUDED.previsto,
                              realizado = EXCLUDED.realizado
                            """),
                            {"okr_id": okr_id, "idx_mes": idx, "ano": m.ano, "mes": m.mes, "previsto": m.previsto, "realizado": m.realizado}
                        )

            # UPSERT actions
            for ac in planning.actions:
                try:
                    _ = datetime.strptime(ac.data_vencimento, "%Y-%m-%d").date()
                    data_v = ac.data_vencimento
                except Exception:
                    data_v = None
                conn.execute(
                    text("""
                    INSERT INTO actions (titulo,area,responsavel,descricao,data_inicio,data_vencimento,status,observacoes)
                    VALUES (:titulo,:area,:responsavel,:descricao,:data_inicio,:data_vencimento,:status,:observacoes)
                    ON CONFLICT (titulo, data_vencimento) DO UPDATE SET
                      area = EXCLUDED.area,
                      responsavel = EXCLUDED.responsavel,
                      descricao = EXCLUDED.descricao,
                      status = EXCLUDED.status,
                      observacoes = EXCLUDED.observacoes
                    """),
                    {"titulo": ac.titulo, "area": ac.area, "responsavel": ac.responsavel, "descricao": ac.descricao, "data_inicio": getattr(ac, "data_inicio", None), "data_vencimento": data_v, "status": ac.status, "observacoes": ac.observacoes}
                )

        return "Exportação para PostgreSQL (Neon) concluída com sucesso (UPSERT)."
    except Exception as e:
        return f"Erro durante exportação para Postgres: {e}"

# -------------------------
# UI helpers: data editor compat and manual editor
# -------------------------

def has_data_editor() -> bool:
    return hasattr(st, "data_editor") or hasattr(st, "experimental_data_editor")


def _filter_kwargs(func, kwargs: dict) -> dict:
    """Keep only kwargs supported by the given Streamlit function (for version compatibility)."""
    try:
        params = set(inspect.signature(func).parameters.keys())
        return {k: v for k, v in kwargs.items() if k in params}
    except Exception:
        # If signature inspection fails, be conservative and pass nothing extra.
        return {}


def try_data_editor(
    df: pd.DataFrame,
    key: Optional[str] = None,
    height: Optional[int] = None,
    column_config=None,
    **kwargs,
) -> Optional[pd.DataFrame]:
    """Compatibility wrapper for Streamlit data editors across versions.

    Some Streamlit versions don't support kwargs like `column_config`, `disabled`,
    `use_container_width`, `num_rows`, etc. This wrapper:
    - accepts them all,
    - filters to what the installed Streamlit supports,
    - falls back gracefully when no editor exists.
    """
    import inspect
    import streamlit as st

    # convenience
    if height is not None:
        kwargs.setdefault("height", height)
    # keep explicit in kwargs so signature mismatches never happen
    if column_config is not None:
        kwargs.setdefault("column_config", column_config)

    editor_fn = None
    if hasattr(st, "data_editor"):
        editor_fn = st.data_editor
    elif hasattr(st, "experimental_data_editor"):
        editor_fn = st.experimental_data_editor

    if editor_fn is None:
        # No editor in this Streamlit version
        st.dataframe(df)
        return df

    # Build call kwargs
    call_kwargs = dict(kwargs)
    if key is not None:
        call_kwargs["key"] = key

    # Filter kwargs to supported params
    try:
        sig = inspect.signature(editor_fn)
        allowed = set(sig.parameters.keys())
        call_kwargs = {k: v for k, v in call_kwargs.items() if k in allowed}
    except Exception:
        pass

    # Try call; if key isn't supported, retry without it
    try:
        return editor_fn(df, **call_kwargs)
    except TypeError:
        call_kwargs.pop("key", None)
        try:
            return editor_fn(df, **call_kwargs)
        except Exception:
            st.dataframe(df)
            return df
    except Exception:
        st.dataframe(df)
        return df


def manual_months_editor(okr: OKR, key_prefix: str) -> Optional[pd.DataFrame]:
    st.markdown("**Editor manual de meses (sem data_editor disponível)**")
    exp = st.expander("Abrir editor manual de 36 meses")
    with exp:
        cols_grid = [st.columns(6) for _ in range(6)]
        vals = []
        for i in range(36):
            row = i // 6
            col = i % 6
            m = okr.meses[i]
            label = f"{i+1} - {m.mes:02d}/{m.ano}"
            key_prev = f"{key_prefix}_prev_{i}"
            key_real = f"{key_prefix}_real_{i}"
            with cols_grid[row][col]:
                prev = st.number_input(label + " Prev", value=float(m.previsto), key=key_prev, format="%.2f")
                real = st.number_input(label + " Real", value=float(m.realizado), key=key_real, format="%.2f")
            vals.append({"m_index": i+1, "ano": m.ano, "mes": m.mes, "previsto": prev, "realizado": real})
        if st.button("Aplicar alterações (editor manual)", key=f"{key_prefix}_apply"):
            return pd.DataFrame(vals)
    return None

# -------------------------
# Formatação / Relatório
# -------------------------

def format_brl(value: float) -> str:
    try:
        v = float(value)
    except Exception:
        return ""
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

HTML_CSS = """
<style>
body {font-family:'Segoe UI', 'Helvetica Neue', Arial, sans-serif; background:#f4f6fb; color:#222; margin:0; padding:0;}
table {width:100%; border-collapse: collapse; margin-top:8px; margin-bottom:12px;}
th, td {border:1px solid #cfcfcf; padding:6px 8px; text-align:left; font-size:13px;}
th {background:#f3f4f6; font-weight:600;}
.section {background:#fff; padding:16px; border-radius:8px; margin-bottom:12px; box-shadow:0 6px 18px rgba(0,0,0,0.06); font-family:'Segoe UI', Arial, sans-serif;}
.header {background:linear-gradient(120deg,#5fb8ff,#bfe9ff); padding:18px 24px; color:#03203c; border-radius:8px; font-family:'Segoe UI', Arial, sans-serif;}
.footer {text-align:center; padding:15px; font-size:12px; color:#555;}
.small-muted {color:#666; font-size:12px;}
.okr-chart {text-align:center; margin:12px 0;}
</style>
"""

HTML_HEADER = f"""
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Relatório BK - Planejamento Estratégico</title>
{HTML_CSS}
</head>
<body>
<div class="header">
  <h1 style="margin:0; font-weight:600;">BK_Planejamento_Estrategico - Planejamento Estratégico (3 anos)</h1>
  <div class="small-muted">Relatório gerado automaticamente</div>
</div>
<div style="padding:18px;">
"""

HTML_FOOTER = """
<div class="footer">Produzido po BK Engenharia e Tecnologia</div>
</div>
</body>
</html>
"""

def generate_recommendations_for_okr(okr: OKR, df: pd.DataFrame) -> List[str]:
    recs = []
    if df.empty:
        return ["Dados insuficientes para recomendações."]
    avg_prev = df['previsto'].mean() if not df['previsto'].isna().all() else 0
    avg_real = df['realizado'].mean() if not df['realizado'].isna().all() else 0
    if avg_prev == 0:
        recs.append("Definir metas previstas (previsto=0 impede análise).")
        return recs
    pct = ((avg_real - avg_prev) / avg_prev) * 100
    if pct < -10:
        recs.append("Realizado consistentemente abaixo do previsto (>10%). Revisar causas.")
    elif pct < 0:
        recs.append("Leve subperformance. Reforçar acompanhamento.")
    elif pct < 10:
        recs.append("Performance adequada. Padronizar processos.")
    else:
        recs.append("Realizado acima do previsto — validar sustentabilidade e ajustar metas se necessário.")
    recs.append("Alinhar OKR com planos de ação e responsáveis com datas claras.")
    return recs

def suggest_okrs_from_data(planning: PlanningData, top_n: int = 5) -> List[str]:
    ideas = []
    areas = [a.area for a in planning.areas] if planning.areas else ["Comercial", "Projetos Elétricos", "Inovação e Tecnologia"]
    if any("forç" in s.tipo.lower() or "oportun" in s.tipo.lower() for s in planning.swot):
        ideas.append(f"Aumentar faturamento recorrente através de serviços de alto valor — Área: {areas[0]}")
    ideas.append("Reduzir lead time de propostas para <48 horas — Área: Comercial")
    ideas.append("Garantir 95% dos entregáveis no prazo em 12 meses — Área: Projetos Elétricos")
    ideas.append("Automatizar 3 relatórios mensais com integração BIM/Python — Área: Inovação e Tecnologia")
    ideas.append("Melhorar margem média por projeto em 5% até o fim do ano — Área: Comercial/Financeiro")
    return ideas[:top_n]

def build_html_report(planning: PlanningData) -> str:
    parts = [HTML_HEADER]
    parts.append(f"<div class='small-muted'>Relatório gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>")

    # Sócios
    parts.append("<div class='section'><h2>Sócios</h2>")
    if planning.partners:
        for p in planning.partners:
            parts.append(f"<h3>{p.nome}</h3>")
            parts.append(f"<div class='small-muted'>{p.cargo}</div>")
            parts.append(f"<div>E-mail: {p.email} | Telefone: {p.telefone}</div>")
            if p.observacoes:
                parts.append(f"<div class='small-muted'>{p.observacoes}</div>")
    else:
        parts.append("<div class='small-muted'>Nenhum sócio cadastrado.</div>")
    parts.append("</div>")

    # Áreas
    parts.append("<div class='section'><h2>Áreas e Responsáveis</h2>")
    if planning.areas:
        parts.append("<table><tr><th>Área</th><th>Responsável</th><th>E-mail</th></tr>")
        for a in planning.areas:
            parts.append(f"<tr><td>{a.area}</td><td>{a.responsavel}</td><td>{a.email}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<div class='small-muted'>Nenhuma área cadastrada.</div>")
    parts.append("</div>")

    # SWOT
    parts.append("<div class='section'><h2>SWOT</h2>")
    if planning.swot:
        parts.append("<table><tr><th>Tipo</th><th>Prioridade</th><th>Descrição</th></tr>")
        for s in planning.swot:
            parts.append(f"<tr><td>{s.tipo}</td><td>{s.prioridade}</td><td>{s.descricao}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<div class='small-muted'>Nenhum item SWOT cadastrado.</div>")
    parts.append("</div>")

    # OKRs & Plans (same as above)
    # (Already included earlier in this file; to keep code single-file and complete, we repeat logic)
    parts.append("<div class='section'><h2>OKRs (3 anos)</h2>")
    if planning.okrs:
        labels, totals_prev, totals_real, pct_real = [], [], [], []
        for o in planning.okrs:
            df = okr_to_dataframe(o)
            tp = df['previsto'].sum()
            tr = df['realizado'].sum()
            labels.append(o.nome)
            totals_prev.append(float(tp))
            totals_real.append(float(tr))
            pct_real.append((tr / tp * 100) if tp != 0 else 0.0)

        fig = go.Figure()
        fig.add_trace(go.Bar(x=labels, y=totals_prev, name='Total Previsto', marker_color='#4c8cff'))
        fig.add_trace(go.Bar(x=labels, y=totals_real, name='Total Realizado', marker_color='#42b983'))
        fig.add_trace(go.Scatter(x=labels, y=pct_real, mode='lines+markers', name='% Realização', yaxis='y2', line=dict(color='black', dash='dash')))
        fig.update_layout(barmode='group', xaxis_tickangle=-15,
                          yaxis=dict(title='Valor (unidade OKR)'),
                          yaxis2=dict(title='% Realização', overlaying='y', side='right'), template='plotly_white', height=360)
        try:
            img = fig.to_image(format="png", width=1100, height=360)
            parts.append(f'<div class="okr-chart"><img src="data:image/png;base64,{base64.b64encode(img).decode("ascii")}" /></div>')
        except Exception:
            pass

        for o in planning.okrs:
            parts.append(f"<h3>{o.nome}</h3>")
            parts.append(f"<div class='small-muted'>Área: {o.area} | Unidade: {o.unidade} | Início: {o.inicio_mes:02d}/{o.inicio_ano}</div>")
            if o.descricao:
                parts.append(f"<p>{o.descricao}</p>")
            df = okr_to_dataframe(o)
            if not df.empty:
                parts.append("<table><tr><th>Mês</th><th>Ano</th><th>Previsto</th><th>Realizado</th></tr>")
                for _, row in df.iterrows():
                    vp = format_brl(row['previsto']) if ("R$" in (o.unidade or "")) else f"{row['previsto']}"
                    vr = format_brl(row['realizado']) if ("R$" in (o.unidade or "")) else f"{row['realizado']}"
                    parts.append(f"<tr><td>{int(row['m_index'])}</td><td>{int(row['ano'])}</td><td>{vp}</td><td>{vr}</td></tr>")
                parts.append("</table>")

                if (df['previsto'].sum() != 0) or (df['realizado'].sum() != 0):
                    fig2 = go.Figure()
                    fig2.add_trace(go.Bar(x=df['m_index'], y=df['previsto'], name='Previsto', marker_color='#4c8cff'))
                    fig2.add_trace(go.Bar(x=df['m_index'], y=df['realizado'], name='Realizado', marker_color='#42b983'))
                    y = df['realizado'].values
                    x = df['m_index'].values
                    if np.count_nonzero(y) >= 3:
                        z = np.polyfit(x, y, 1)
                        p = np.poly1d(z)
                        trend_y = p(x)
                        fig2.add_trace(go.Scatter(x=x, y=trend_y, mode='lines', name='Tendência (Realizado)', line=dict(color='black', dash='dash')))
                    fig2.update_layout(barmode='group', xaxis_title='Mês (1-36)', yaxis_title=f'Valor ({o.unidade})', template='plotly_white', height=340)
                    try:
                        img2 = fig2.to_image(format="png", width=1100, height=340)
                        parts.append(f'<div class="okr-chart"><img src="data:image/png;base64,{base64.b64encode(img2).decode("ascii")}" /></div>')
                    except Exception:
                        pass

                recs = generate_recommendations_for_okr(o, df)
                parts.append("<h4>Recomendações</h4><ul>")
                for r in recs:
                    parts.append(f"<li>{r}</li>")
                parts.append("</ul>")
    else:
        parts.append("<div class='small-muted'>Nenhuma OKR cadastrada.</div>")
    parts.append("</div>")

    # Planos
    parts.append("<div class='section'><h2>Planos de Ação</h2>")
    if planning.actions:
        parts.append("<table><tr><th>Título</th><th>Área</th><th>Responsável</th><th>Vencimento</th><th>Status</th></tr>")
        for ac in planning.actions:
            parts.append(f"<tr><td>{ac.titulo}</td><td>{ac.area}</td><td>{ac.responsavel}</td><td>{ac.data_vencimento}</td><td>{ac.status}</td></tr>")
        parts.append("</table>")

        df_actions = pd.DataFrame([asdict(a) for a in planning.actions])
        try:
            df_actions['data_dt'] = pd.to_datetime(df_actions['data_vencimento'], errors='coerce')
            df_actions['year_month'] = df_actions['data_dt'].dt.to_period('M')
            if df_actions['year_month'].notna().any():
                min_period = df_actions['year_month'].min()
                max_period = df_actions['year_month'].max()
                periods = pd.period_range(start=min_period, end=max_period, freq='M')
                labels = [p.strftime("%Y-%m") for p in periods]
                total_due = []
                pct_done = []
                for p in periods:
                    sel = df_actions[df_actions['year_month'] == p]
                    total = len(sel)
                    done = len(sel[sel['status'] == 'Concluído'])
                    total_due.append(total)
                    pct_done.append((done / total * 100) if total > 0 else 0.0)
                fig_a = go.Figure()
                fig_a.add_trace(go.Bar(x=labels, y=total_due, name='Planos com vencimento', marker_color='#ff7f0e'))
                fig_a.add_trace(go.Scatter(x=labels, y=pct_done, name='% concluídos', yaxis='y2', mode='lines+markers', line=dict(color='black', dash='dash')))
                fig_a.update_layout(xaxis_tickangle=-45, yaxis=dict(title='Qtde'), yaxis2=dict(title='% concluídos', overlaying='y', side='right'), template='plotly_white', height=340)
                try:
                    img_a = fig_a.to_image(format="png", width=1100, height=340)
                    parts.append(f'<div class="okr-chart"><img src="data:image/png;base64,{base64.b64encode(img_a).decode("ascii")}" /></div>')
                except Exception:
                    pass
        except Exception:
            pass
    else:
        parts.append("<div class='small-muted'>Nenhum plano de ação cadastrado.</div>")
    parts.append("</div>")

    parts.append(HTML_FOOTER)
    return "\n".join(parts)

# -------------------------
# STREAMLIT APP - UI
# -------------------------

st.set_page_config(page_title="BK_Planejamento_Estrategico", layout="wide", initial_sidebar_state="expanded")

# Header
st.markdown(
    """
    <div style="background:linear-gradient(120deg,#c9edff,#78b6ff); padding:18px 24px; border-radius:8px;">
        <h1 style="color:#03203c; margin:4px 0; font-family:'Segoe UI', Arial, sans-serif;">BK_Planejamento_Estrategico</h1>
        <p style="color:#052b45; margin:0; font-family:'Segoe UI', Arial, sans-serif;">Planejamento Estratégico (3 anos) — Produzido po BK Engenharia e Tecnologia</p>
    </div>
    """,
    unsafe_allow_html=True
)

# Load or initialize planning
if "planning" not in st.session_state:
    if os.path.exists("planning.json"):
        try:
            with open("planning.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            st.session_state.planning = PlanningData.from_dict(data)
        except Exception:
            st.session_state.planning = PlanningData()
    else:
        st.session_state.planning = PlanningData()

planning: PlanningData = st.session_state.planning

def save_session_planning(pl: PlanningData):
    st.session_state.planning = pl

# Sidebar
with st.sidebar:
    st.header("Dados / Exportação")
    uploaded = st.file_uploader("Abrir JSON (planning)", type=["json"], key="sidebar_uploader")
    if uploaded:
        try:
            data = json.load(uploaded)
            st.session_state.planning = PlanningData.from_dict(data)
            planning = st.session_state.planning
            st.success("Arquivo JSON carregado com sucesso.")
        except Exception as e:
            st.error(f"Erro ao ler JSON: {e}")

    st.download_button("Exportar JSON", data=json.dumps(planning.to_dict(), ensure_ascii=False, indent=2).encode("utf-8"),
                       file_name="planning_export.json", mime="application/json", key="dl_json")

    st.markdown("---")
    st.subheader("Exportar para Power BI / Arquivos")
    if st.button("Exportar (ZIP com CSVs)", key="btn_export_zip"):
        zip_bytes = export_to_csv_zip(planning)
        st.download_button("Baixar CSVs (.zip)", data=zip_bytes, file_name="planning_csvs.zip", mime="application/zip", key="dl_zip")
    if st.button("Exportar (Excel multi-aba)", key="btn_export_xlsx"):
        xlsx_bytes = export_to_excel_bytes(planning)
        st.download_button("Baixar Excel", data=xlsx_bytes, file_name="planning_multi_sheet.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_xlsx")

    st.markdown("---")
    st.subheader("Exportar para Neon (Postgres)")
    default_conn = ""
    try:
        default_conn = st.secrets.get("neon", {}).get("connection", "")
    except Exception:
        pass
    conn_str = st.text_input("Connection string PostgreSQL (Neon)", value=os.environ.get("NEON_DATABASE_URL", default_conn), help="Ex: postgresql://user:senha@endpoint:5432/dbname?sslmode=require", key="neon_conn")
    if st.button("Exportar para Neon", key="btn_export_neon"):
        if not conn_str:
            st.error("Informe a connection string do Neon/Postgres.")
        else:
            with st.spinner("Exportando para Neon/Postgres..."):
                res = export_to_postgres(planning, conn_str)
                if res.startswith("Erro"):
                    st.error(res)
                else:
                    st.success(res)

    st.markdown("---")
    st.subheader("Relatório")
    if st.button("Gerar relatório HTML (download)", key="btn_gen_report"):
        html = build_html_report(planning)
        st.download_button("Baixar relatório HTML", data=html.encode('utf-8'), file_name="relatorio_planejamento.html", mime="text/html", key="dl_report")
        st.success("Relatório pronto para download.")

    st.markdown("---")
    st.subheader("Sugestões de OKRs")
    for i, item in enumerate(suggest_okrs_from_data(planning)):
        st.write(f"{i+1}. {item}")


# Tabs
tabs = st.tabs(["Sócios", "Informações Estratégicas", "Áreas e Responsáveis", "SWOT", "OKRs (36 meses)", "Planos de Ação", "Relatórios"])

# ---- Sócios ----
with tabs[0]:
    st.subheader("Sócios")
    c1, c2 = st.columns([2, 1])
    with c1:
        nome = st.text_input("Nome", key="partners_nome")
        cargo = st.text_input("Cargo", key="partners_cargo")
        email = st.text_input("E-mail (sócio)", key="partners_email")
        telefone = st.text_input("Telefone", key="partners_telefone")
        observacoes = st.text_area("Observações (sócio)", height=80, key="partners_obs")
        if st.button("Adicionar sócio", key="partners_add"):
            if not nome.strip():
                st.warning("Informe nome do sócio.")
            else:
                planning.partners.append(Partner(nome=nome, cargo=cargo, email=email, telefone=telefone, observacoes=observacoes))
                save_session_planning(planning)
                st.success("Sócio adicionado.")
    with c2:
        df = pd.DataFrame([asdict(p) for p in planning.partners]) if planning.partners else pd.DataFrame(columns=["nome","cargo","email","telefone","observacoes"])
        st.dataframe(df, height=260, key="partners_df")
        sel = st.selectbox("Selecionar índice para excluir (sócio)", options=["Nenhum"] + [str(i) for i in range(len(planning.partners))], key="partners_sel")
        if sel != "Nenhum" and st.button("Excluir selecionado (sócio)", key="partners_del"):
            planning.partners.pop(int(sel))
            save_session_planning(planning)
            st.success("Sócio excluído.")

# ---- Informações Estratégicas ----
with tabs[1]:
    st.subheader("Informações Estratégicas (Visão, Missão, Valores…)")
    st.caption("Pense nesta aba como o “norte” da empresa. Ela alimenta os relatórios e dá coerência às decisões, OKRs e planos de ação.")
    s = planning.strategic

    colA, colB = st.columns(2)
    with colA:
        s.visao = st.text_area("Visão (onde queremos chegar em 3–5 anos)", value=s.visao, height=110, key="st_visao")
        s.missao = st.text_area("Missão (por que existimos / o que entregamos)", value=s.missao, height=110, key="st_missao")
        s.proposta_valor = st.text_area("Proposta de valor (o que o cliente compra de verdade)", value=s.proposta_valor, height=110, key="st_pv")
        s.publico_alvo = st.text_area("Público-alvo (segmentos, ICP, decisores)", value=s.publico_alvo, height=110, key="st_publico")
    with colB:
        s.valores = st.text_area("Valores (comportamentos inegociáveis)", value=s.valores, height=110, key="st_valores")
        s.posicionamento = st.text_area("Posicionamento (como queremos ser percebidos)", value=s.posicionamento, height=110, key="st_posicionamento")
        s.diferenciais = st.text_area("Diferenciais competitivos (por que ganharíamos?)", value=s.diferenciais, height=110, key="st_diferenciais")
        s.pilares = st.text_area("Pilares estratégicos (3–6 frentes)", value=s.pilares, height=110, key="st_pilares")

    s.objetivos_estrategicos = st.text_area("Objetivos estratégicos (alto nível, 6–12 meses)", value=s.objetivos_estrategicos, height=130, key="st_objetivos")
    s.notas = st.text_area("Notas / hipóteses / restrições importantes", value=s.notas, height=90, key="st_notas")

    planning.strategic = s
    save_session_planning(planning)

    with st.expander("Dicas (modelo rápido para preencher)"):
        st.markdown("""
- **Visão**: verbo + impacto + prazo. Ex.: “Ser referência regional em ___ até 2029”.
- **Missão**: público + entrega + diferencial. Ex.: “Ajudar ___ a ___ com ___”.
- **Pilares**: 3–6 temas. Ex.: Crescimento, Eficiência, Pessoas, Inovação, Qualidade, Sustentabilidade.
- **Objetivos estratégicos**: 6–10 bullets, conectando com OKRs.
""")

# ---- Áreas e Responsáveis ----
with tabs[2]:
    st.subheader("Áreas e Responsáveis")
    c1, c2 = st.columns([2, 1])
    with c1:
        area = st.text_input("Área", key="areas_area")
        responsavel = st.text_input("Responsável", key="areas_responsavel")
        area_email = st.text_input("E-mail (área)", key="areas_email")
        area_obs = st.text_area("Observações (área)", height=80, key="areas_obs")
        if st.button("Adicionar área", key="areas_add"):
            if not area.strip():
                st.warning("Informe a área.")
            else:
                planning.areas.append(AreaResponsavel(area=area, responsavel=responsavel, email=area_email, observacoes=area_obs))
                save_session_planning(planning)
                st.success("Área adicionada.")
    with c2:
        df = pd.DataFrame([asdict(a) for a in planning.areas]) if planning.areas else pd.DataFrame(columns=["area","responsavel","email","observacoes"])
        st.dataframe(df, height=260, key="areas_df")
        sel = st.selectbox("Selecionar índice para excluir (área)", options=["Nenhum"] + [str(i) for i in range(len(planning.areas))], key="areas_sel")
        if sel != "Nenhum" and st.button("Excluir selecionado (área)", key="areas_del"):
            planning.areas.pop(int(sel))
            save_session_planning(planning)
            st.success("Área excluída.")

# ---- SWOT ----
with tabs[3]:
    st.subheader("SWOT")
    st.caption("Sugestão: mantenha a SWOT concisa, priorizando itens que realmente geram decisões e OKRs.")
    df_swot = pd.DataFrame([asdict(s) for s in planning.swot]) if planning.swot else pd.DataFrame(columns=["tipo","descricao","prioridade"])
    if "Excluir" not in df_swot.columns:
        df_swot["Excluir"] = False

    edited = try_data_editor(
        df_swot,
        key="swot_editor",
        height=420,
        column_config={
            "tipo": st.column_config.SelectboxColumn("Tipo", options=["Força","Fraqueza","Oportunidade","Ameaça"], required=True),
            "prioridade": st.column_config.SelectboxColumn("Prioridade", options=["Alta","Média","Baixa"], required=True),
            "Excluir": st.column_config.CheckboxColumn("Excluir")
        }
    )
    if edited is not None and st.button("Aplicar alterações (SWOT)", key="swot_apply"):
        new_items = []
        for _, r in edited.iterrows():
            if bool(r.get("Excluir", False)):
                continue
            desc = str(r.get("descricao","")).strip()
            if not desc:
                continue
            new_items.append(SWOTItem(tipo=str(r.get("tipo","Força")), descricao=desc, prioridade=str(r.get("prioridade","Média"))))
        planning.swot = new_items
        save_session_planning(planning)
        st.success("SWOT atualizada.")

# ---- OKRs ----
def _okr_meta_df(pl: PlanningData) -> pd.DataFrame:
    rows = []
    for i, o in enumerate(pl.okrs, start=1):
        try:
            d0 = date(int(o.inicio_ano), int(o.inicio_mes), 1)
        except Exception:
            d0 = date.today().replace(day=1)
        rows.append({
            "okr_id": i,
            "OKR": o.nome,
            "Área": o.area,
            "Unidade": o.unidade or "Inteiro",
            "Descrição": o.descricao,
            "Início": d0,
            "Excluir": False
        })
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["okr_id","OKR","Área","Unidade","Descrição","Início","Excluir"])
    return df

def _sync_okrs_from_meta(pl: PlanningData, df_meta: pd.DataFrame) -> None:
    # Mantém mapeamento por okr_id quando existir; novas linhas (okr_id vazio/0) viram novas OKRs
    existing_by_id = {i+1: pl.okrs[i] for i in range(len(pl.okrs))}
    new_okrs: List[OKR] = []
    next_id = 1
    for _, r in df_meta.iterrows():
        if bool(r.get("Excluir", False)):
            continue
        nome = str(r.get("OKR","")).strip()
        if not nome:
            continue
        area = str(r.get("Área","")).strip()
        unidade = str(r.get("Unidade","Inteiro")).strip()
        desc = str(r.get("Descrição","")).strip()
        inicio = r.get("Início", None)
        if isinstance(inicio, (datetime, date)):
            inicio_ano = int(inicio.year)
            inicio_mes = int(inicio.month)
        else:
            inicio_ano = date.today().year
            inicio_mes = date.today().month

        rid = r.get("okr_id", None)
        try:
            rid = int(rid)
        except Exception:
            rid = None

        if rid in existing_by_id:
            o = existing_by_id[rid]
            o.nome = nome
            o.area = area
            o.unidade = unidade
            o.descricao = desc
            # Se mudou início, recalcula 36 meses preservando valores já preenchidos por índice (M1..M36)
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
            o = OKR(nome=nome, area=area, unidade=unidade, descricao=desc, inicio_ano=inicio_ano, inicio_mes=inicio_mes)
            o.__post_init__()
            new_okrs.append(o)
        next_id += 1

    pl.okrs = new_okrs

def _okr_wide_df(pl: PlanningData, kind: str) -> pd.DataFrame:
    assert kind in ("previsto","realizado")
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
        if okr_name not in name_to_okr:
            continue
        o = name_to_okr[okr_name]
        unit = (o.unidade or "Inteiro").strip()
        for k in range(36):
            col = f"M{k+1:02d}"
            v = r.get(col, 0.0)
            try:
                fv = float(v) if v != "" else 0.0
            except Exception:
                fv = 0.0
            if unit.lower().startswith("inte"):
                fv = int(round(fv))
            if kind == "previsto":
                o.meses[k].previsto = fv
            else:
                o.meses[k].realizado = fv

def _month_labels_for_okr(o: OKR) -> List[str]:
    # Garante sempre 36 rótulos (M01..M36) a partir do início cadastrado,
    # mesmo que a lista o.meses esteja vazia/curta (compatibilidade com dados antigos).
    labels: List[str] = []
    ano = int(getattr(o, "inicio_ano", date.today().year) or date.today().year)
    mes = int(getattr(o, "inicio_mes", date.today().month) or date.today().month)
    for _ in range(36):
        labels.append(f"{mes:02d}/{ano}")
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return labels

with tabs[4]:
    st.subheader("OKRs (cadastro direto + 36 meses)")
    st.caption("1) Cadastre/edite OKRs na tabela. 2) Preencha Previsto (planejado) e Realizado. 3) Veja comparação + gráficos e gere relatório HTML.")

    st.markdown("### 1) Cadastro de OKRs (editável)")
    df_meta = _okr_meta_df(planning)

    unidade_opts = ["R$", "%", "Inteiro"]
    edited_meta = try_data_editor(
        df_meta,
        key="okr_meta_editor",
        height=280,
        disabled=["okr_id"],
        column_config={
            "Unidade": st.column_config.SelectboxColumn("Unidade", options=unidade_opts, required=True),
            "Início": st.column_config.DateColumn("Início", format="MM/YYYY"),
            "Excluir": st.column_config.CheckboxColumn("Excluir")
        }
    )
    cmeta1, cmeta2 = st.columns([1, 2])
    with cmeta1:
        if st.button("Aplicar cadastro de OKRs", key="okr_meta_apply"):
            if edited_meta is not None:
                _sync_okrs_from_meta(planning, edited_meta)
                save_session_planning(planning)
                st.success("OKRs atualizadas.")
            else:
                st.warning("Seu Streamlit não suportou o editor. Atualize para uma versão recente do Streamlit.")
    with cmeta2:
        st.info("Dica: para criar uma OKR nova, adicione uma linha na tabela (na última linha em branco). Marque **Excluir** para remover.")

    if not planning.okrs:
        st.warning("Cadastre ao menos 1 OKR para liberar as tabelas de Previsto/Realizado.")
    else:
        okr_names = [o.nome for o in planning.okrs]

        st.markdown("### 2) Planejado (Previsto) — 36 colunas (M01…M36)")
        st.caption("As colunas M01..M36 correspondem aos 36 meses a partir do **Início** cadastrado em cada OKR.")
        df_prev = _okr_wide_df(planning, "previsto")
        edited_prev = try_data_editor(
            df_prev,
            key="okr_prev_editor",
            height=320,
            disabled=["okr_id","Unidade"],
            column_config={
                "OKR": st.column_config.SelectboxColumn("OKR", options=okr_names, required=True),
                "Unidade": st.column_config.TextColumn("Unidade")
            }
        )
        if st.button("Salvar Planejado (Previsto)", key="okr_prev_save"):
            if edited_prev is not None:
                _apply_wide_to_okrs(planning, edited_prev, "previsto")
                save_session_planning(planning)
                st.success("Planejado salvo.")

        st.markdown("### 3) Realizado — 36 colunas (M01…M36)")
        df_real = _okr_wide_df(planning, "realizado")
        edited_real = try_data_editor(
            df_real,
            key="okr_real_editor",
            height=320,
            disabled=["okr_id","Unidade"],
            column_config={
                "OKR": st.column_config.SelectboxColumn("OKR", options=okr_names, required=True),
                "Unidade": st.column_config.TextColumn("Unidade")
            }
        )
        if st.button("Salvar Realizado", key="okr_real_save"):
            if edited_real is not None:
                _apply_wide_to_okrs(planning, edited_real, "realizado")
                save_session_planning(planning)
                st.success("Realizado salvo.")

        st.markdown("### 4) Comparação (não editável) + gráficos")
        sel_okr = st.selectbox("Escolha a OKR para análise", options=okr_names, key="okr_analysis_sel")
        okr_obj = next((o for o in planning.okrs if o.nome == sel_okr), None)
        if okr_obj:
            labels = _month_labels_for_okr(okr_obj)
            # Garante vetores com 36 posições (se dados antigos tiverem menos meses, completa com 0)
            prev = [float(okr_obj.meses[k].previsto) if k < len(okr_obj.meses) else 0.0 for k in range(36)]
            real = [float(okr_obj.meses[k].realizado) if k < len(okr_obj.meses) else 0.0 for k in range(36)]
            diff = [r - p for r, p in zip(real, prev)]
            status = []
            for d in diff:
                if d > 0: status.append("Realizado > Planejado")
                elif d < 0: status.append("Realizado < Planejado")
                else: status.append("Igual")

            df_cmp = pd.DataFrame({
                "Mês": labels,
                "Planejado": prev,
                "Realizado": real,
                "Diferença": diff,
                "Status": status
            })
            st.dataframe(df_cmp, use_container_width=True, height=420)

            k1, k2, k3 = st.columns(3)
            k1.metric("Total Planejado", f"{sum(prev):,.2f}")
            k2.metric("Total Realizado", f"{sum(real):,.2f}")
            k3.metric("Diferença Total", f"{sum(diff):,.2f}")

            if st.button("Gerar gráficos (Planejado x Realizado)", key="okr_make_charts"):
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=labels, y=prev, mode="lines+markers", name="Planejado"))
                fig.add_trace(go.Scatter(x=labels, y=real, mode="lines+markers", name="Realizado"))
                fig.update_layout(template="plotly_white", height=360, xaxis_tickangle=-45, title=f"{sel_okr} — Planejado x Realizado")
                st.plotly_chart(fig, use_container_width=True)

                fig2 = go.Figure()
                fig2.add_trace(go.Bar(x=labels, y=diff, name="Diferença (Real - Plan)"))
                fig2.update_layout(template="plotly_white", height=320, xaxis_tickangle=-45, title="Diferença mensal")
                st.plotly_chart(fig2, use_container_width=True)

                cum_prev = np.cumsum(prev).tolist()
                cum_real = np.cumsum(real).tolist()
                fig3 = go.Figure()
                fig3.add_trace(go.Scatter(x=labels, y=cum_prev, mode="lines+markers", name="Acumulado Planejado"))
                fig3.add_trace(go.Scatter(x=labels, y=cum_real, mode="lines+markers", name="Acumulado Realizado"))
                fig3.update_layout(template="plotly_white", height=360, xaxis_tickangle=-45, title="Acumulado")
                st.plotly_chart(fig3, use_container_width=True)

            with st.expander("Legenda dos meses (M01..M36) para esta OKR"):
                safe_labels = (labels + [""] * 36)[:36]
                st.write(pd.DataFrame({"Coluna": [f"M{k+1:02d}" for k in range(36)], "Mês": safe_labels}))

        st.markdown("### 5) Relatório HTML moderno (OKRs)")
        if st.button("Gerar relatório HTML (download)", key="okr_html_report"):
            html = build_html_report(planning)
            st.download_button("Baixar relatório HTML", data=html.encode("utf-8"), file_name="relatorio_okrs.html", mime="text/html", key="dl_okr_html")
            st.success("Relatório gerado.")

# ---- Planos de Ação ----
def _actions_df(pl: PlanningData) -> pd.DataFrame:
    rows = []
    for a in pl.actions:
        rows.append({
            "Título": a.titulo,
            "Área": a.area,
            "Responsável": a.responsavel,
            "Descrição": a.descricao,
            "Início": a.data_inicio if hasattr(a, "data_inicio") else "",
            "Vencimento": a.data_vencimento,
            "Status": a.status,
            "Observações": a.observacoes,
            "Excluir": False
        })
    cols = ["Título","Área","Responsável","Descrição","Início","Vencimento","Status","Observações","Excluir"]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)

def _sync_actions(pl: PlanningData, df: pd.DataFrame) -> None:
    new_actions = []
    for _, r in df.iterrows():
        if bool(r.get("Excluir", False)):
            continue
        titulo = str(r.get("Título","")).strip()
        if not titulo:
            continue
        area = str(r.get("Área","")).strip()
        resp = str(r.get("Responsável","")).strip()
        desc = str(r.get("Descrição","")).strip()
        inicio = str(r.get("Início","")).strip() or date.today().strftime("%Y-%m-%d")
        venc = str(r.get("Vencimento","")).strip() or date.today().strftime("%Y-%m-%d")
        status = str(r.get("Status","Pendente")).strip()
        obs = str(r.get("Observações","")).strip()
        new_actions.append(PlanoAcao(
            titulo=titulo, area=area, responsavel=resp, descricao=desc,
            data_inicio=inicio, data_vencimento=venc, status=status, observacoes=obs
        ))
    pl.actions = new_actions

with tabs[5]:
    st.subheader("Planos de Ação (cadastro direto + relatórios)")
    st.caption("Registre iniciativas que destravam as OKRs. Aqui você edita direto na tabela e acompanha atrasos e conclusão.")

    df_act = _actions_df(planning)
    edited_act = try_data_editor(
        df_act,
        key="actions_editor",
        height=360,
        column_config={
            "Status": st.column_config.SelectboxColumn("Status", options=["Pendente","Em andamento","Concluído"], required=True),
            "Excluir": st.column_config.CheckboxColumn("Excluir")
        }
    )
    if st.button("Salvar Planos de Ação", key="actions_save"):
        if edited_act is not None:
            _sync_actions(planning, edited_act)
            save_session_planning(planning)
            st.success("Planos de ação salvos.")

    # Analytics
    st.markdown("### Painel de acompanhamento")
    today = date.today()
    dfa = _actions_df(planning)
    if not dfa.empty:
        def _parse_dt(s):
            try:
                return datetime.strptime(str(s), "%Y-%m-%d").date()
            except Exception:
                return None

        dfa2 = dfa.copy()
        dfa2["dt_inicio"] = dfa2["Início"].apply(_parse_dt)
        dfa2["dt_venc"] = dfa2["Vencimento"].apply(_parse_dt)
        dfa2["Atraso (dias)"] = dfa2.apply(
            lambda r: (today - r["dt_venc"]).days if (r["dt_venc"] and r["Status"] != "Concluído" and r["dt_venc"] < today) else 0,
            axis=1
        )
        total = len(dfa2)
        done = int((dfa2["Status"] == "Concluído").sum())
        doing = int((dfa2["Status"] == "Em andamento").sum())
        pending = int((dfa2["Status"] == "Pendente").sum())
        overdue = int((dfa2["Atraso (dias)"] > 0).sum())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", total)
        c2.metric("Concluídos", done)
        c3.metric("Em andamento", doing)
        c4.metric("Atrasados", overdue)

        if st.button("Gerar gráficos (Planos)", key="actions_charts"):
            # Atrasos
            df_over = dfa2[dfa2["Atraso (dias)"] > 0].sort_values("Atraso (dias)", ascending=False).head(20)
            if not df_over.empty:
                fig = go.Figure()
                fig.add_trace(go.Bar(x=df_over["Título"], y=df_over["Atraso (dias)"], name="Atraso (dias)"))
                fig.update_layout(template="plotly_white", height=360, xaxis_tickangle=-45, title="Top atrasos (dias)")
                st.plotly_chart(fig, use_container_width=True)
            # Status
            fig2 = go.Figure()
            fig2.add_trace(go.Bar(x=["Pendente","Em andamento","Concluído"], y=[pending, doing, done], name="Qtde"))
            fig2.update_layout(template="plotly_white", height=280, title="Distribuição por status")
            st.plotly_chart(fig2, use_container_width=True)

        if st.button("Gerar relatório HTML (Planos de Ação)", key="actions_html"):
            html = build_html_report(planning)
            st.download_button("Baixar relatório HTML", data=html.encode("utf-8"), file_name="relatorio_planos_acao.html", mime="text/html", key="dl_actions_html")
            st.success("Relatório gerado.")

    else:
        st.info("Nenhum plano de ação cadastrado ainda.")

# ---- Relatórios ----
with tabs[6]:
    st.subheader("Relatórios & Direcionamento")
    st.caption("Aqui você consolida ‘para onde a empresa está indo’, com base em Visão/Missão, SWOT, OKRs e execução.")

    s = planning.strategic
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### Norte estratégico")
        st.write(f"**Visão:** {s.visao or '—'}")
        st.write(f"**Missão:** {s.missao or '—'}")
        st.write(f"**Pilares:** {s.pilares or '—'}")
    with col2:
        st.markdown("### Proposta & mercado")
        st.write(f"**Proposta de valor:** {s.proposta_valor or '—'}")
        st.write(f"**Público-alvo:** {s.publico_alvo or '—'}")
        st.write(f"**Diferenciais:** {s.diferenciais or '—'}")

    st.markdown("### Sinais de execução (OKRs + Planos)")
    if planning.okrs:
        # quick health: % meses com realizado preenchido
        rows = []
        for o in planning.okrs:
            real_vals = [m.realizado for m in o.meses[:36]]
            filled = sum(1 for v in real_vals if float(v) != 0.0)
            rows.append({"OKR": o.nome, "Área": o.area, "Unidade": o.unidade, "% meses com realizado": round(filled/36*100,1)})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=240)
    else:
        st.info("Sem OKRs cadastradas.")

    if planning.actions:
        dfp = _actions_df(planning)
        st.dataframe(dfp.drop(columns=["Excluir"]), use_container_width=True, height=240)
    else:
        st.info("Sem planos de ação cadastrados.")

    st.markdown("### Recomendações de rota")
    recs = []
    if planning.swot:
        # heurística simples
        threats = [s for s in planning.swot if s.tipo == "Ameaça" and s.prioridade == "Alta"]
        opps = [s for s in planning.swot if s.tipo == "Oportunidade" and s.prioridade == "Alta"]
        if threats:
            recs.append("Tratar **Ameaças (Alta)** com planos de mitigação e responsáveis claros (prazo + risco + contingência).")
        if opps:
            recs.append("Transformar **Oportunidades (Alta)** em 1–2 OKRs por pilar, com métricas objetivas.")
    if planning.actions:
        # atrasos
        try:
            dfa = _actions_df(planning)
            overdue = 0
            for _, r in dfa.iterrows():
                if r["Status"] != "Concluído":
                    try:
                        dv = datetime.strptime(str(r["Vencimento"]), "%Y-%m-%d").date()
                        if dv < date.today():
                            overdue += 1
                    except Exception:
                        pass
            if overdue:
                recs.append(f"Há **{overdue}** plano(s) atrasado(s). Priorize replanejamento: escopo, capacidade, bloqueios e nova data.")
        except Exception:
            pass
    if planning.okrs:
        recs.append("Crie uma rotina de gestão: **revisão mensal** (realizado) + **revisão trimestral** (OKRs e prioridades).")

    if not recs:
        recs = ["Preencha Visão/Missão/Pilares e cadastre OKRs e planos para gerar recomendações automáticas."]

    for r in recs:
        st.write("• " + r)

    st.markdown("### Relatório HTML completo")
    if st.button("Gerar relatório HTML (completo)", key="full_html_report"):
        html = build_html_report(planning)
        st.components.v1.html(html, height=900, scrolling=True)
        st.download_button("Baixar relatório HTML", data=html.encode("utf-8"), file_name="relatorio_completo.html", mime="text/html", key="dl_full_html")
        st.success("Relatório gerado.")

# Salvar local
if st.button("Salvar dados em planning.json", key="save_local"):
    with open("planning.json", "w", encoding="utf-8") as f:
        json.dump(planning.to_dict(), f, ensure_ascii=False, indent=2)
    st.success("Dados salvos em planning.json")

st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
st.markdown("<footer style='text-align:center;color:#666;'>Produzido por BK Engenharia e Tecnologia</footer>", unsafe_allow_html=True)
