# app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import math
from datetime import datetime

app = FastAPI(title="Rooted Valuation Backend", version="0.2.0")

# CORS (frontends on Netlify etc.)
app.add_middleware(
   CORSMiddleware,
   allow_origins=["*"],  # tighten later
   allow_credentials=True,
   allow_methods=["*"],
   allow_headers=["*"],
)

# ---------- Request / Response Models ----------
class ValuationRequest(BaseModel):
   collections_2024: float
   collections_2025: float
   # friendly optional fields (may be blank / zero)
   region: Optional[str] = ""
   practice_type: Optional[str] = ""
   ops: Optional[int] = 0
   equipped_ops: Optional[int] = 0
   sqft: Optional[int] = 0
   active_patients: Optional[int] = 0
   hygiene_pct: Optional[float] = 0.0        # 0.00 - 1.00
   ebitda_margin_pct: Optional[float] = 0.0   # 0.00 - 1.00

class ValuationResponse(BaseModel):
   final_value: float
   dcf_value: float
   asset_value_total: float
   goodwill_value: float
   weights: dict
   rationale: List[str]

# ---------- Helpers ----------
def clamp(x, lo, hi):
   return max(lo, min(hi, x))

def region_adjustment(region: str) -> float:
   """
   Light-touch location adjustment. Ontario is our beta.
   Positive for GTA; small negatives possible for remote areas.
   """
   r = (region or "").lower()
   if "gta" in r or "toronto" in r:
       return 0.02   # +2%
   if "ottawa" in r or "waterloo" in r:
       return 0.01   # +1%
   if "muskoka" in r or "simcoe" in r:
       return 0.00
   if "northern" in r:
       return -0.01  # -1%
   return 0.0

def practice_adjustment(practice_type: str) -> float:
   """
   General dentistry baseline; specialties can command higher goodwill multiples.
   """
   pt = (practice_type or "").lower()
   if "orth" in pt:        # Orthodontics
       return 0.15
   if "endo" in pt:        # Endodontics
       return 0.10
   if "oral" in pt:        # Oral Surgery
       return 0.10
   if "pedo" in pt:        # Pediatric
       return 0.05
   if "perio" in pt:       # Periodontics
       return 0.05
   return 0.00             # GP default

def infer_margin(hygiene_pct: float, ebitda_margin_pct: float) -> float:
   """
   If EBITDA margin provided, use it.
   Else infer from hygiene mix with conservative bounds.
   """
   if ebitda_margin_pct and ebitda_margin_pct > 0:
       return clamp(ebitda_margin_pct, 0.08, 0.35)
   # very rough: hygiene >= 30% tends to correlate with healthier profitability
   h = hygiene_pct or 0.0
   base = 0.16 + max(0.0, h - 0.30) * 0.35   # each extra hygiene point nudges margin
   return clamp(base, 0.12, 0.28)

def dcf_5y(rev0: float, margin: float, g: float, r: float) -> float:
   """
   Simple 5-year DCF on EBITDA-like cash flows + Gordon terminal.
   rev0 grows by g; cash flow = rev_t * margin.
   """
   pv = 0.0
   rev = rev0
   for t in range(1, 6):
       rev = rev * (1.0 + g)
       cf = rev * margin
       pv += cf / ((1.0 + r) ** t)
   # terminal with conservative g_term
   g_term = 0.02
   cf5 = rev * margin
   tv = cf5 * (1.0 + g_term) / (r - g_term)  # Gordon growth
   pv += tv / ((1.0 + r) ** 5)
   return max(pv, 0.0)

# ---------- Three Rails ----------
def goodwill_rail(c24: float, c25: float, region: str, practice_type: str) -> float:
   # Broker-style weighted revenue
   wr = (3.0 * c25 + 2.0 * c24) / 5.0
   province_factor = 0.95  # Ontario GP baseline
   adj = region_adjustment(region) + practice_adjustment(practice_type)
   goodwill = wr * province_factor * (1.0 + adj)
   return max(goodwill, 0.0)

def asset_rail(goodwill: float, sqft: int, ops: int, equipped_ops: int) -> float:
   # Leaseholds
   leaseholds_per_sqft = 300.0   # matches broker heuristic in your sample
   remaining_life_pct = 0.6857   # from their example doc
   leaseholds = (sqft or 0) * leaseholds_per_sqft * remaining_life_pct

   # Equipment (favor equipped ops; fall back to % of total ops)
   eq_ops = equipped_ops or int(round((ops or 0) * 0.7))
   equipment_per_op = 40000.0    # conservative mid-point for Ontario GP beta
   equipment = max(0, eq_ops) * equipment_per_op

   # Supplies
   supplies = 35000.0 if (ops or 0) > 0 else 20000.0

   return max(goodwill + leaseholds + equipment + supplies, 0.0)

def income_rail(c25: float, margin: float, region: str) -> float:
   # Minor risk adjustments: better rate for GTA-scale demand
   r = 0.20
   if "gta" in (region or "").lower() or "toronto" in (region or "").lower():
       r = 0.18
   g = 0.03  # forward growth
   return dcf_5y(rev0=c25, margin=margin, g=g, r=r)

def choose_weights(margin: float) -> dict:
   """
   Profitability-aware weighting.
   """
   if margin < 0.12:
       return {"income": 0.20, "goodwill": 0.40, "asset": 0.40}
   if margin < 0.18:
       return {"income": 0.30, "goodwill": 0.35, "asset": 0.35}
   if margin < 0.24:
       return {"income": 0.45, "goodwill": 0.35, "asset": 0.20}
   return {"income": 0.55, "goodwill": 0.30, "asset": 0.15}

def valuate_core(req: ValuationRequest) -> ValuationResponse:
   c24 = max(req.collections_2024 or 0.0, 0.0)
   c25 = max(req.collections_2025 or 0.0, 0.0)

   margin = infer_margin(req.hygiene_pct or 0.0, req.ebitda_margin_pct or 0.0)

   goodwill = goodwill_rail(c24, c25, req.region or "", req.practice_type or "")
   asset_total = asset_rail(goodwill, req.sqft or 0, req.ops or 0, req.equipped_ops or 0)
   dcf_val = income_rail(c25, margin, req.region or "")

   w = choose_weights(margin)
   final_val = w["income"] * dcf_val + w["goodwill"] * goodwill + w["asset"] * asset_total

   rationale = [
       f"Weighted revenue goodwill: province_factor=0.95, region/practice adj applied ({req.region or 'n/a'}, {req.practice_type or 'n/a'}).",
       f"Asset floor: leaseholds (~$300/sqft, life 68.57%), equipment (~$40k per equipped op), supplies baseline.",
       f"Income (DCF): 5y with margin={margin:.2%}, growth=3%, discount={'18%' if 'gta' in (req.region or '').lower() or 'toronto' in (req.region or '').lower() else '20%'}",
       f"Weights (by profitability): {w}.",
   ]

   return ValuationResponse(
       final_value=float(final_val),
       dcf_value=float(dcf_val),
       asset_value_total=float(asset_total),
       goodwill_value=float(goodwill),
       weights=w,
       rationale=rationale,
   )

# ---------- API ----------
@app.get("/health")
def health():
   return {"ok": True, "version": app.version, "time": datetime.utcnow().isoformat() + "Z"}

@app.post("/api/valuate", response_model=ValuationResponse)
def valuate(req: ValuationRequest):
   return valuate_core(req)