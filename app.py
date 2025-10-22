import os
from datetime import date
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, select_autoescape
from xhtml2pdf import pisa
from io import BytesIO
from valuation import Inputs, compute

APP_BRAND = os.getenv("ROOTED_BRAND", "Rooted.ai")

app = FastAPI(title="Rooted Valuation API", version="0.1.0")

origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

env = Environment(loader=FileSystemLoader(searchpath="./"), autoescape=select_autoescape())
tpl = env.get_template("report_template.html")

class ValuationIn(BaseModel):
    collections_2024: float
    collections_2025: float
    equipment_value: float = 0
    leasehold_value: float = 0
    supplies_value: float = 0
    benchmark_pct: float = 0.95
    adjustment_pct: float = 0.02
    goodwill_pct: float = 0.97
    margin_pct: float = 0.20
    growth_pct: float = 0.05
    years: int = 5
    discount_rate: float = 0.20
    terminal_rev_pct: float = 0.80

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/api/valuate")
def valuate(payload: ValuationIn):
    out = compute(Inputs(**payload.model_dump()))
    return JSONResponse({
        "weighted_revenue": out.weighted_revenue,
        "goodwill": out.goodwill,
        "tangible_assets": out.tangible_assets,
        "dcf_value": out.dcf_value,
        "asset_value_total": out.asset_value_total,
        "final_value": out.final_value,
        "rationale": out.rationale
    })

@app.post("/api/valuate/pdf")
def valuate_pdf(payload: ValuationIn):
    out = compute(Inputs(**payload.model_dump()))
    html = tpl.render(
        brand=APP_BRAND,
        date=str(date.today()),
        final_value=out.final_value,
        dcf_value=out.dcf_value,
        asset_total=out.asset_value_total,
        c24=payload.collections_2024,
        c25=payload.collections_2025,
        equipment=payload.equipment_value,
        leasehold=payload.leasehold_value,
        supplies=payload.supplies_value,
        margin=payload.margin_pct,
        growth=payload.growth_pct,
        discount=payload.discount_rate,
        weighted_revenue=out.weighted_revenue,
        goodwill=out.goodwill,
        tangible=out.tangible_assets,
        rationale=out.rationale
    )
    pdf_io = BytesIO()
    pisa.CreatePDF(html, dest=pdf_io)
    pdf_io.seek(0)
    return StreamingResponse(pdf_io, media_type="application/pdf",
                             headers={"Content-Disposition":"attachment; filename=valuation.pdf"})
