# BK Planejamento Estratégico v2.0
**BK Engenharia e Tecnologia**

---

## 📦 Estrutura dos Arquivos

```
planejamento_estrategico/
├── streamlit_app.py          # App principal (Streamlit)
├── api_fastapi.py            # API REST para integração/Power BI
├── generate_report_docx.py   # Gerador de relatório Word (.docx)
├── planning.json             # Dados salvos localmente
├── requirements.txt          # Dependências completas
├── requirements_app.txt      # Dependências mínimas (Streamlit Cloud)
├── .streamlit/
│   └── secrets.toml          # Credenciais (NÃO commitar no Git)
└── README.md                 # Este arquivo
```

---

## 🚀 Como Rodar

### Streamlit (interface principal)
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

### API FastAPI
```bash
uvicorn api_fastapi:app --reload --port 8000
# Endpoints:
# GET /planning          → JSON completo
# GET /planning/csv      → ZIP com CSVs
# GET /planning/excel    → Excel multi-aba
# GET /planning/okrs     → Apenas OKRs
# GET /planning/actions  → Apenas Planos
```

### Relatório Word (.docx)
```bash
python generate_report_docx.py
# Gera: relatorio_planejamento.docx
```

---

## 🌐 Deploy no Streamlit Cloud

1. Suba o projeto para um repositório GitHub
2. Em `Settings > Secrets`, adicione:
   ```toml
   [neon]
   connection = "postgresql://user:senha@endpoint:5432/db?sslmode=require"
   ```
3. Use `requirements_app.txt` como arquivo de dependências

---

## ✨ O que há de novo na v2.0

### Layout
- Header gradiente BK (azul → teal)
- Paleta de cores consistente em todo o app
- KPI cards no topo com semáforos automáticos
- Sidebar dark com botões organizados
- CSS customizado para Streamlit

### Gráficos Novos
- **Dashboard**: visão geral consolidada de todas OKRs
- **Gauge (velocímetro)**: % realização por OKR com cores automáticas
- **SWOT visual**: matriz 4-quadrantes interativa com bolhas
- **Gantt**: linha do tempo dos planos de ação (timeline)
- **Donut**: distribuição de status dos planos
- **Atraso por responsável**: bar chart de responsabilidade
- **OKR mensal**: subplots com diferença + tendência
- **Acumulado**: área preenchida previsto vs realizado

### Funcionalidades
- Tabelas 100% editáveis (num_rows="dynamic") em todas as abas
- Campos com tipos corretos: SelectboxColumn, DateColumn, NumberColumn
- Áreas e responsáveis como dropdown nas tabelas de planos
- Tab Dashboard com alertas de atrasos
- Relatório HTML moderno com KPIs, badges e gráficos embutidos

### Correções de bugs
- `build_example()` implementado corretamente
- `StrategicInfo` protegido contra chaves extras no JSON
- `conn_str` com `type="password"` (senha mascarada)
- Typo "Produzido por" corrigido
- `data_inicio` adicionado nos exemplos de `generate_report_docx.py`
- API não importa mais o módulo Streamlit inteiro

---

## 📋 Abas do App

| Aba | Conteúdo |
|-----|----------|
| 🏠 Dashboard | KPIs + gráficos consolidados + alertas |
| 👥 Sócios | Cadastro e tabela editável |
| 🧭 Estratégia | Visão, Missão, Valores, Pilares |
| 🏢 Áreas | Responsáveis por área |
| ⚖️ SWOT | Matriz visual + tabela editável |
| 📈 OKRs | Previsto/Realizado 36 meses + análise |
| ✅ Planos | Kanban analytics + Gantt |
| 📄 Relatórios | Exportação HTML/Excel/ZIP |

---

*Produzido por BK Engenharia e Tecnologia*
