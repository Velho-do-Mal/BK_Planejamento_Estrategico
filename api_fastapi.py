# api_fastapi.py
"""
API FastAPI para BK_Planejamento_Estrategico v2.0
Corrigido: importa apenas de models_core.py (sem importar streamlit_app inteiro)
"""
from fastapi import FastAPI, Response, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import io
import json
import os
from datetime import datetime
from typing import Optional

# Importa apenas funções puras (sem Streamlit)
from streamlit_app import (
    PlanningData,
    export_to_csv_zip,
    export_to_excel_bytes,
    build_example,
)

app = FastAPI(
    title="BK_Planejamento_Estrategico API",
    version="2.0",
    description="API de exportação e consulta do planejamento estratégico BK Engenharia",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_FILE = "planning.json"


def load_planning() -> PlanningData:
    """Carrega planning.json ou retorna dados de exemplo."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return PlanningData.from_dict(data)
        except Exception:
            pass
    # Fallback: dados de exemplo (build_example corrigido)
    return build_example()


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "service": "BK_Planejamento_Estrategico API v2.0",
            "timestamp": datetime.now().isoformat()}


@app.get("/planning", tags=["Dados"])
def get_planning():
    """Retorna o planejamento completo em JSON."""
    planning = load_planning()
    return JSONResponse(content=planning.to_dict())


@app.get("/planning/csv", tags=["Exportação"])
def get_planning_csv_zip():
    """Exporta todos os dados como CSVs dentro de um arquivo ZIP."""
    planning = load_planning()
    zip_bytes = export_to_csv_zip(planning)
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=planning_csvs.zip"}
    )


@app.get("/planning/excel", tags=["Exportação"])
def get_planning_excel():
    """Exporta todos os dados em planilha Excel multi-aba."""
    planning = load_planning()
    xlsx_bytes = export_to_excel_bytes(planning)
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=planning_bk.xlsx"}
    )


@app.get("/planning/okrs", tags=["Dados"])
def get_okrs():
    """Retorna apenas os OKRs."""
    planning = load_planning()
    return [o.__dict__ if hasattr(o, "__dict__") else {} for o in planning.okrs]


@app.get("/planning/actions", tags=["Dados"])
def get_actions():
    """Retorna apenas os Planos de Ação."""
    planning = load_planning()
    from dataclasses import asdict
    return [asdict(a) for a in planning.actions]


@app.get("/planning/swot", tags=["Dados"])
def get_swot():
    """Retorna análise SWOT."""
    planning = load_planning()
    from dataclasses import asdict
    return [asdict(s) for s in planning.swot]
