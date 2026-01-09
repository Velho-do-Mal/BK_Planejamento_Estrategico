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
    data_vencimento: str  # "YYYY-MM-DD"
    status: str = "Pendente"
    observacoes: str = ""


@dataclass
class PlanningData:
    partners: List[Partner] = field(default_factory=list)
    areas: List[AreaResponsavel] = field(default_factory=list)
    swot: List[SWOTItem] = field(default_factory=list)
    okrs: List[OKR] = field(default_factory=list)
    actions: List[PlanoAcao] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
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
            pd_obj.okrs.append(okr)
        for ac in data.get("actions", []):
            pd_obj.actions.append(PlanoAcao(**ac))
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
    df_actions = pd.DataFrame([asdict(a) for a in planning.actions]) if planning.actions else pd.DataFrame(columns=["titulo","area","responsavel","descricao","data_vencimento","status","observacoes"])
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
                    INSERT INTO actions (titulo,area,responsavel,descricao,data_vencimento,status,observacoes)
                    VALUES (:titulo,:area,:responsavel,:descricao,:data_vencimento,:status,:observacoes)
                    ON CONFLICT (titulo, data_vencimento) DO UPDATE SET
                      area = EXCLUDED.area,
                      responsavel = EXCLUDED.responsavel,
                      descricao = EXCLUDED.descricao,
                      status = EXCLUDED.status,
                      observacoes = EXCLUDED.observacoes
                    """),
                    {"titulo": ac.titulo, "area": ac.area, "responsavel": ac.responsavel, "descricao": ac.descricao, "data_vencimento": data_v, "status": ac.status, "observacoes": ac.observacoes}
                )

        return "Exportação para PostgreSQL (Neon) concluída com sucesso (UPSERT)."
    except Exception as e:
        return f"Erro durante exportação para Postgres: {e}"

# -------------------------
# UI helpers: data editor compat and manual editor
# -------------------------

def has_data_editor() -> bool:
    return hasattr(st, "data_editor") or hasattr(st, "experimental_data_editor")


def try_data_editor(df: pd.DataFrame, key: str, height: Optional[int] = None) -> Optional[pd.DataFrame]:
    try:
        if hasattr(st, "data_editor"):
            return st.data_editor(df, key=key, num_rows="fixed", use_container_width=True)
        elif hasattr(st, "experimental_data_editor"):
            return st.experimental_data_editor(df, key=key, num_rows="fixed")
        else:
            return None
    except Exception:
        return None


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
tabs = st.tabs(["Sócios", "Áreas e Responsáveis", "SWOT", "OKR (3 anos)", "Planos de Ação", "Relatório & Insights"])

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
        if st.button("Adicionar / Atualizar sócio", key="partners_add"):
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

# ---- Áreas e Responsáveis ----
with tabs[1]:
    st.subheader("Áreas e Responsáveis")
    c1, c2 = st.columns([2, 1])
    with c1:
        area = st.text_input("Área", key="areas_area")
        responsavel = st.text_input("Responsável", key="areas_responsavel")
        area_email = st.text_input("E-mail (área)", key="areas_email")
        area_obs = st.text_area("Observações (área)", height=80, key="areas_obs")
        if st.button("Adicionar / Atualizar área", key="areas_add"):
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
with tabs[2]:
    st.subheader("SWOT")
    tipo = st.selectbox("Tipo", ["Força", "Fraqueza", "Oportunidade", "Ameaça"], key="swot_tipo")
    prioridade = st.selectbox("Prioridade", ["Alta", "Média", "Baixa"], key="swot_prioridade")
    desc = st.text_area("Descrição (SWOT)", height=140, key="swot_desc")
    col_add, col_edit = st.columns(2)
    with col_add:
        if st.button("Adicionar SWOT", key="swot_add"):
            if not desc.strip():
                st.warning("Informe descrição do item SWOT.")
            else:
                planning.swot.append(SWOTItem(tipo=tipo, descricao=desc, prioridade=prioridade))
                save_session_planning(planning)
                st.success("Item SWOT adicionado.")
    with col_edit:
        sel_swot = st.selectbox("Selecionar índice para editar (SWOT)", options=["Nenhum"] + [str(i) for i in range(len(planning.swot))], key="swot_sel")
        if sel_swot != "Nenhum":
            idx = int(sel_swot)
            item = planning.swot[idx]
            edit_tipo = st.selectbox("Tipo - edição", ["Força", "Fraqueza", "Oportunidade", "Ameaça"], index=["Força","Fraqueza","Oportunidade","Ameaça"].index(item.tipo) if item.tipo in ["Força","Fraqueza","Oportunidade","Ameaça"] else 0, key="swot_edit_tipo")
            edit_prio = st.selectbox("Prioridade - edição", ["Alta","Média","Baixa"], index=["Alta","Média","Baixa"].index(item.prioridade) if item.prioridade in ["Alta","Média","Baixa"] else 1, key="swot_edit_prio")
            edit_desc = st.text_area("Descrição - edição", value=item.descricao, key="swot_edit_desc", height=120)
            if st.button("Salvar alteração (SWOT)", key="swot_save"):
                planning.swot[idx] = SWOTItem(tipo=edit_tipo, descricao=edit_desc, prioridade=edit_prio)
                save_session_planning(planning)
                st.success("Item SWOT atualizado.")
            if st.button("Excluir item (SWOT)", key="swot_delete"):
                planning.swot.pop(idx)
                save_session_planning(planning)
                st.success("Item SWOT excluído.")
    df = pd.DataFrame([asdict(s) for s in planning.swot]) if planning.swot else pd.DataFrame(columns=["tipo","descricao","prioridade"])
    st.dataframe(df, height=360, key="swot_df")

# ---- OKR (3 anos) ----
with tabs[3]:
    st.subheader("OKR (3 anos)")
    left, right = st.columns([2, 2])

    # left: create/edit
    with left:
        if "okr_edit_idx" not in st.session_state:
            st.session_state.okr_edit_idx = None

        if st.session_state.okr_edit_idx is not None:
            idx_edit = st.session_state.okr_edit_idx
            obj = planning.okrs[idx_edit]
            okr_nome = st.text_input("Nome da OKR", value=obj.nome, key="okr_nome")
            okr_area = st.text_input("Área (OKR)", value=obj.area, key="okr_area")
            okr_unidade = st.text_input("Unidade (ex: R$, %)", value=obj.unidade, key="okr_unidade")
            inicio_ano = st.number_input("Início - Ano", value=obj.inicio_ano, step=1, key="okr_inicio_ano")
            inicio_mes = st.number_input("Início - Mês", value=obj.inicio_mes, min_value=1, max_value=12, key="okr_inicio_mes")
            okr_desc = st.text_area("Descrição (OKR)", value=obj.descricao, height=120, key="okr_desc")
        else:
            okr_nome = st.text_input("Nome da OKR", key="okr_nome_new")
            okr_area = st.text_input("Área (OKR)", key="okr_area_new")
            okr_unidade = st.text_input("Unidade (ex: R$, %)", key="okr_unidade_new")
            inicio_ano = st.number_input("Início - Ano", value=date.today().year, step=1, key="okr_inicio_ano_new")
            inicio_mes = st.number_input("Início - Mês", value=date.today().month, min_value=1, max_value=12, key="okr_inicio_mes_new")
            okr_desc = st.text_area("Descrição (OKR)", height=120, key="okr_desc_new")

        bc1, bc2, bc3 = st.columns(3)
        with bc1:
            if st.button("Salvar OKR", key="okr_save"):
                try:
                    if st.session_state.okr_edit_idx is not None:
                        i = st.session_state.okr_edit_idx
                        okr = planning.okrs[i]
                        okr.nome = okr_nome or okr.nome
                        okr.area = okr_area or okr.area
                        okr.unidade = okr_unidade or okr.unidade
                        okr.inicio_ano = int(inicio_ano)
                        okr.inicio_mes = int(inicio_mes)
                        okr.descricao = okr_desc
                        if len(okr.meses) < 36:
                            okr.__post_init__()
                        save_session_planning(planning)
                        st.success("OKR atualizada.")
                        st.session_state.okr_edit_idx = None
                    else:
                        new_okr = OKR(nome=okr_nome or "OKR sem nome", area=okr_area, unidade=okr_unidade or "unidade", descricao=okr_desc, inicio_ano=int(inicio_ano), inicio_mes=int(inicio_mes))
                        new_okr.__post_init__()
                        planning.okrs.append(new_okr)
                        save_session_planning(planning)
                        st.success("OKR adicionada.")
                except Exception as e:
                    st.error(f"Erro ao salvar OKR: {e}")
        with bc2:
            if st.button("Cancelar edição", key="okr_cancel"):
                st.session_state.okr_edit_idx = None
        with bc3:
            st.markdown("**Preencher Previsto em massa**")
            bulk_val = st.number_input("Valor Previsto (todas meses)", value=0.0, format="%.2f", key="okr_bulk_val")
            sel_okr_bulk = st.selectbox("Selecionar OKR para aplicar", options=["Nenhum"] + [str(i) for i in range(len(planning.okrs))], key="okr_bulk_sel")
            if st.button("Aplicar", key="okr_bulk_apply"):
                if sel_okr_bulk != "Nenhum":
                    oidx = int(sel_okr_bulk)
                    for m in planning.okrs[oidx].meses:
                        m.previsto = float(bulk_val)
                    save_session_planning(planning)
                    st.success("Previstos atualizados para todos os meses da OKR.")

    # right: list, filters, aggregated table, monthly editor
    with right:
        st.markdown("### Lista de OKRs")
        df_list = pd.DataFrame([{"idx": i, "nome": o.nome, "area": o.area, "unidade": o.unidade, "inicio": f"{o.inicio_mes:02d}/{o.inicio_ano}"} for i, o in enumerate(planning.okrs)]) if planning.okrs else pd.DataFrame(columns=["idx","nome","area","unidade","inicio"])
        st.dataframe(df_list.drop(columns=["idx"]) if not df_list.empty else df_list, height=220, key="okrs_list")

        st.markdown("Filtros para tabela agregada")
        okr_filter_options = ["Todas"] + [o.nome for o in planning.okrs]
        okr_filter = st.selectbox("Filtrar por OKR", options=okr_filter_options, key="okr_filter")
        col_period = st.columns(2)
        start_m = int(col_period[0].number_input("Início (mês índice 1-36)", min_value=1, max_value=36, value=1, key="okr_period_start"))
        end_m = int(col_period[1].number_input("Fim (mês índice 1-36)", min_value=1, max_value=36, value=36, key="okr_period_end"))
        rows = []
        for i, o in enumerate(planning.okrs):
            if okr_filter != "Todas" and o.nome != okr_filter:
                continue
            dfm = okr_to_dataframe(o)
            df_sel = dfm[(dfm['m_index'] >= start_m) & (dfm['m_index'] <= end_m)]
            total_prev = df_sel['previsto'].sum()
            total_real = df_sel['realizado'].sum()
            diff = total_real - total_prev
            pct = (total_real / total_prev * 100) if total_prev != 0 else None
            last_real_idx = dfm[dfm['realizado'] != 0]['m_index'].max() if not dfm[dfm['realizado'] != 0].empty else None
            last_update = None
            if last_real_idx:
                mm = dfm.loc[dfm['m_index'] == last_real_idx].iloc[0]
                last_update = f"{int(mm['mes']):02d}/{int(mm['ano'])}"
            rows.append({
                "OKR": o.nome,
                "Área": o.area,
                "Unidade": o.unidade,
                "Início": f"{o.inicio_mes:02d}/{o.inicio_ano}",
                f"Previsto ({start_m}-{end_m})": total_prev,
                f"Realizado ({start_m}-{end_m})": total_real,
                "Diferença": diff,
                "% Realização": f"{pct:.1f}%" if pct is not None else "-",
                "Última atualização (realizado)": last_update or "-"
            })
        df_ag = pd.DataFrame(rows)
        if not df_ag.empty:
            if any("R$" in (o.unidade or "") for o in planning.okrs):
                for col in df_ag.columns:
                    if "Previsto" in col or "Realizado" in col or "Diferença" in col:
                        df_ag[col] = df_ag[col].apply(lambda v: format_brl(v) if pd.notna(v) and v != 0 else ("R$ 0,00" if pd.notna(v) else ""))
            st.dataframe(df_ag, height=400, key="okrs_agg_table")
        else:
            st.info("Nenhuma OKR encontrada para os filtros selecionados.")

        sel = st.selectbox("Selecionar OKR para editar meses", options=["Nenhum"] + [str(i) for i in range(len(planning.okrs))], key="okr_month_sel")
        if sel != "Nenhum":
            idx_sel = int(sel)
            o = planning.okrs[idx_sel]
            st.markdown(f"#### Edição mensal - {o.nome}")
            df_months = okr_to_dataframe(o)

            edited = try_data_editor(df_months[['m_index','ano','mes','previsto','realizado']], key=f"okr_dataeditor_{idx_sel}", height=420)
            df_result = None
            if edited is not None:
                if st.button("Aplicar alterações (data editor)", key=f"okr_apply_de_{idx_sel}"):
                    df_result = edited
            else:
                df_result = manual_months_editor(o, key_prefix=f"okr_manual_{idx_sel}")

            if df_result is not None:
                for _, row in df_result.iterrows():
                    i = int(row['m_index']) - 1
                    if 0 <= i < len(o.meses):
                        try:
                            o.meses[i].previsto = float(row['previsto'])
                        except Exception:
                            o.meses[i].previsto = 0.0
                        try:
                            o.meses[i].realizado = float(row['realizado'])
                        except Exception:
                            o.meses[i].realizado = 0.0
                save_session_planning(planning)
                st.success("Meses atualizados.")

            if (df_months['previsto'].sum() != 0) or (df_months['realizado'].sum() != 0):
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df_months['m_index'], y=df_months['previsto'], mode='lines+markers', name='Previsto'))
                fig.add_trace(go.Scatter(x=df_months['m_index'], y=df_months['realizado'], mode='lines+markers', name='Realizado'))
                st.plotly_chart(fig, use_container_width=True)

        st.markdown("#### Ações na OKR")
        sel_ops = st.selectbox("Selecionar índice para editar/excluir", options=["Nenhum"] + [str(i) for i in range(len(planning.okrs))], key="okr_ops_sel")
        if sel_ops != "Nenhum":
            idx_ops = int(sel_ops)
            cedit, cdel = st.columns(2)
            with cedit:
                if st.button("Editar OKR (pré-carregar)", key=f"okr_edit_btn_{idx_ops}"):
                    st.session_state.okr_edit_idx = idx_ops
                    try:
                        st.experimental_rerun()
                    except Exception:
                        pass
            with cdel:
                if st.button("Excluir OKR", key=f"okr_delete_btn_{idx_ops}"):
                    planning.okrs.pop(idx_ops)
                    save_session_planning(planning)
                    st.success("OKR excluída.")
                    try:
                        st.experimental_rerun()
                    except Exception:
                        pass

# ---- Planos de Ação ----
with tabs[4]:
    st.subheader("Planos de Ação")
    left, right = st.columns([2, 1])

    if "action_edit_idx" not in st.session_state:
        st.session_state.action_edit_idx = None

    with left:
        if st.session_state.action_edit_idx is not None:
            aidx = st.session_state.action_edit_idx
            ac = planning.actions[aidx]
            titulo = st.text_input("Título", value=ac.titulo, key="ac_titulo")
            area_action = st.text_input("Área (plano)", value=ac.area, key="ac_area")
            resp = st.text_input("Responsável (plano)", value=ac.responsavel, key="ac_resp")
            try:
                dt = datetime.strptime(ac.data_vencimento, "%Y-%m-%d").date()
            except Exception:
                dt = date.today()
            data_venc = st.date_input("Data de vencimento", value=dt, key="ac_dt")
            status = st.selectbox("Status (plano)", ["Pendente","Em andamento","Concluído"], index=["Pendente","Em andamento","Concluído"].index(ac.status) if ac.status in ["Pendente","Em andamento","Concluído"] else 0, key="ac_status")
            desc = st.text_area("Descrição (plano)", value=ac.descricao, height=120, key="ac_desc")
            obs = st.text_area("Observações (plano)", value=ac.observacoes, height=80, key="ac_obs")
            if st.button("Salvar alteração (plano)", key="ac_save"):
                planning.actions[aidx] = PlanoAcao(titulo=titulo, area=area_action, responsavel=resp, descricao=desc, data_vencimento=data_venc.strftime("%Y-%m-%d"), status=status, observacoes=obs)
                save_session_planning(planning)
                st.session_state.action_edit_idx = None
                st.success("Plano atualizado.")
            if st.button("Cancelar edição (plano)", key="ac_cancel"):
                st.session_state.action_edit_idx = None
        else:
            titulo = st.text_input("Título", key="ac_titulo_new")
            area_action = st.text_input("Área (plano)", key="ac_area_new")
            resp = st.text_input("Responsável (plano)", key="ac_resp_new")
            data_venc = st.date_input("Data de vencimento", key="ac_dt_new")
            status = st.selectbox("Status (plano)", ["Pendente","Em andamento","Concluído"], key="ac_status_new")
            desc = st.text_area("Descrição (plano)", height=120, key="ac_desc_new")
            obs = st.text_area("Observações (plano)", height=80, key="ac_obs_new")
            if st.button("Adicionar plano", key="ac_add"):
                planning.actions.append(PlanoAcao(titulo=titulo, area=area_action, responsavel=resp, descricao=desc, data_vencimento=data_venc.strftime("%Y-%m-%d"), status=status, observacoes=obs))
                save_session_planning(planning)
                st.success("Plano adicionado.")

    with right:
        dfa = pd.DataFrame([asdict(a) for a in planning.actions]) if planning.actions else pd.DataFrame(columns=["titulo","area","responsavel","data_vencimento","status","observacoes"])
        st.dataframe(dfa, height=420, key="actions_df")
        sel = st.selectbox("Selecionar índice para editar/excluir (plano)", options=["Nenhum"] + [str(i) for i in range(len(planning.actions))], key="actions_sel")
        if sel != "Nenhum":
            idx = int(sel)
            if st.button("Editar plano selecionado", key=f"action_edit_btn_{idx}"):
                st.session_state.action_edit_idx = idx
                try:
                    st.experimental_rerun()
                except Exception:
                    pass
            if st.button("Excluir plano selecionado", key=f"action_delete_btn_{idx}"):
                planning.actions.pop(idx)
                save_session_planning(planning)
                st.success("Plano excluído.")
                try:
                    st.experimental_rerun()
                except Exception:
                    pass

# ---- Relatório & Insights ----
with tabs[5]:
    st.subheader("Relatório & Insights")
    if st.button("Gerar e visualizar relatório HTML (preview)", key="preview_report"):
        html = build_html_report(planning)
        st.components.v1.html(html, height=900, scrolling=True)
        st.download_button("Baixar relatório HTML", data=html.encode('utf-8'), file_name="relatorio_planejamento.html", mime="text/html", key="dl_report_preview")
    st.markdown("### Ideias de OKRs automáticas")
    for idea in suggest_okrs_from_data(planning):
        st.write("- " + idea)

# Salvar local
if st.button("Salvar dados em planning.json", key="save_local"):
    with open("planning.json", "w", encoding="utf-8") as f:
        json.dump(planning.to_dict(), f, ensure_ascii=False, indent=2)
    st.success("Dados salvos em planning.json")

st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
st.markdown("<footer style='text-align:center;color:#666;'>Produzido po BK Engenharia e Tecnologia</footer>", unsafe_allow_html=True)
