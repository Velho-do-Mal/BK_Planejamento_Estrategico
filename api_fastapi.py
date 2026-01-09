# api_fastapi.py
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
import io
import zipfile
import json
import os

from streamlit_app import PlanningData, export_to_csv_zip  # reaproveita a função do streamlit_app

app = FastAPI(title="BK_Planejamento_Estrategico API", version="1.0")

# Carrega planning.json se existir para servir como fonte inicial
DATA_FILE = "planning.json"

def load_planning_from_file() -> PlanningData:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return PlanningData.from_dict(data)
    else:
        # se não existir, retorna um exemplo via Streamlit's builder
        from streamlit_app import build_example
        return build_example()

@app.get("/planning")
def get_planning():
    planning = load_planning_from_file()
    return planning.to_dict()

@app.get("/planning/csv")
def get_planning_csv_zip():
    planning = load_planning_from_file()
    zip_bytes = export_to_csv_zip(planning)
    return StreamingResponse(io.BytesIO(zip_bytes), media_type="application/zip", headers={"Content-Disposition":"attachment; filename=planning_csvs.zip"})
