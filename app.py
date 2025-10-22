# app.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

app = FastAPI(title="Rooted Valuation Backend", version="0.3.0")

# CORS (tighten later)
app.add_middleware(
   CORSMiddleware,
   allow_origins=["*"],
   allow_credentials=True,
   allow_methods=["*"],
   allow_headers=["*"],
)

# ---------- Models ----------
class ValuationRequest(BaseModel):
   collections_2024: float
   collections_2025: float
   region: Optional[str] = ""
   practice_type: Optional[str] = ""
   ops: Optional[int] = 0
   equipped_ops: Optional[int] = 0
   sqft: Optional[int] = 0
   active_patients: Optional[int] = 0
   hygiene_pct: Optional[float] = 0.0         # 0.00–1.00
   ebitda_margin_pct: Optional[float] = 0.0    # 0.00–1.00

class ValuationResponse(BaseModel):
   final_value: float
   dcf_value: float
   asset_value_total: float       # physical assets only (leaseholds+equipment+supplies)
   goodwill_value: float
   weights: Dict[str, float]
   rationale: List[str]

# ---------- Helpers / Heuristics ----------
def clamp(x: float, lo: float, hi: float) -> float:
   return max(lo, min(hi, x))

def region_adjustment(region: str) -> float:
   r = (region or "").lower()
   if "gta" in r or "toronto" in r: return 0.02
   if "ottawa" in r or "waterloo" in r: return 0.01
   if "northern" in r: return -0.01
   return 0.00

def practice_adjustment(practice_type: str) -> float:
   pt = (practice_type or "").lower()
   if "orth" in pt:  return 0.15   # Orthodontics
   if "endo" in pt:  return 0.10   # Endodontics
   if "oral" in pt:  return 0.10   # Oral surgery
   if "pedo" in pt:  return 0.05   # Pediatric
   if "perio" in pt: return 0.05   # Periodontics
   return 0.00                     # GP default

def infer_margin(hygiene_pct: float, ebitda_margin_pct: float) -> float:
   if ebitda_margin_pct and ebitda_margin_pct > 0:
       return clamp(ebitda_margin_pct, 0.08, 0.35)
   h = hygiene_pct or 0.0
   # base 16%, improve with hygiene% above 30
   base = 0.16 + max(0.0, h - 0.30) * 0.35
   return clamp(base, 0.12, 0.28)

def dcf_5y(rev0: float, margin: float, growth: float, discount: float) -> float:
   pv, rev = 0.0, rev0
   for t in range(1, 6):
       rev *= (1.0 + growth)
       cf = rev * margin
       pv += cf / ((1.0 + discount) ** t)
   g_term = 0.02
   cf5 = rev * margin
   tv = cf5 * (1.0 + g_term) / (discount - g_term)
   pv += tv / ((1.0 + discount) ** 5)
   return max(pv, 0.0)

# ---------- Rails ----------
def goodwill_rail(c24: float, c25: float, region: str, practice_type: str) -> Dict[str, float]:
   wr = (3.0 * c25 + 2.0 * c24) / 5.0                  # broker-style weighted revenue
   province_factor = 0.95                               # Ontario GP baseline
   adj = region_adjustment(region) + practice_adjustment(practice_type)
   goodwill = max(wr * province_factor * (1.0 + adj), 0.0)
   return {
       "weighted_revenue": wr,
       "province_factor": province_factor,
       "adjustments": adj,
       "goodwill": goodwill,
   }

def asset_rail_only(sqft: int, ops: int, equipped_ops: int) -> Dict[str, float]:
   leaseholds_psf = 300.0
   remaining_life = 0.6857
   leaseholds = (sqft or 0) * leaseholds_psf * remaining_life

   eq_ops = equipped_ops or int(round((ops or 0) * 0.7))
   equip_per_op = 40000.0
   equipment = max(0, eq_ops) * equip_per_op

   supplies = 35000.0 if (ops or 0) > 0 else 20000.0
   total_assets = max(leaseholds + equipment + supplies, 0.0)
   return {
       "leaseholds": leaseholds,
       "equipment": equipment,
       "supplies": supplies,
       "assets_only": total_assets,
   }

def income_rail(c25: float, margin: float, region: str) -> Dict[str, float]:
   discount = 0.18 if ("gta" in (region or "").lower() or "toronto" in (region or "").lower()) else 0.20
   growth = 0.03
   dcfv = dcf_5y(rev0=c25, margin=margin, growth=growth, discount=discount)
   return {
       "margin": margin,
       "growth": growth,
       "discount": discount,
       "dcf_value": dcfv,
   }

def pick_income_weight(margin: float) -> float:
   if margin < 0.12: return 0.30
   if margin < 0.18: return 0.40
   if margin < 0.24: return 0.50
   return 0.60

# ---------- Core valuation ----------
def compute_components(req: ValuationRequest) -> Dict[str, Any]:
   c24 = max(req.collections_2024 or 0.0, 0.0)
   c25 = max(req.collections_2025 or 0.0, 0.0)

   margin = infer_margin(req.hygiene_pct or 0.0, req.ebitda_margin_pct or 0.0)

   gw = goodwill_rail(c24, c25, req.region or "", req.practice_type or "")
   ar = asset_rail_only(req.sqft or 0, req.ops or 0, req.equipped_ops or 0)
   ir = income_rail(c25, margin, req.region or "")

   asset_plus_goodwill = gw["goodwill"] + ar["assets_only"]
   w_income = pick_income_weight(ir["margin"])
   w_assetgw = 1.0 - w_income
   final_val = w_income * ir["dcf_value"] + w_assetgw * asset_plus_goodwill

   return {
       "inputs": req.model_dump(),
       "goodwill": gw,
       "assets": ar,
       "income": ir,
       "blending": {
           "asset_plus_goodwill": asset_plus_goodwill,
           "weights": {"income": w_income, "asset_plus_goodwill": w_assetgw},
           "final_value": final_val,
       }
   }

def valuate_core(req: ValuationRequest) -> ValuationResponse:
   comp = compute_components(req)
   gw = comp["goodwill"]["goodwill"]
   assets_only = comp["assets"]["assets_only"]
   dcfv = comp["income"]["dcf_value"]
   w = comp["blending"]["weights"]
   final_val = comp["blending"]["final_value"]

   rationale = [
       "Goodwill via weighted revenue (Ontario 0.95) + region/practice adjustments.",
       "Assets: leaseholds (~$300/sqft × 68.57% remaining), equipment (~$40k per equipped op), supplies baseline.",
       f"Income: 5y DCF with margin={comp['income']['margin']:.2%}, g={comp['income']['growth']:.0%}, "
       f"discount={comp['income']['discount']:.0%}.",
       f"Final = {w['income']:.0%}×Income + {w['asset_plus_goodwill']:.0%}×(Assets+Goodwill).",
   ]

   return ValuationResponse(
       final_value=float(final_val),
       dcf_value=float(dcfv),
       asset_value_total=float(assets_only),
       goodwill_value=float(gw),
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

@app.post("/api/debug/rails")
def debug_rails(req: ValuationRequest):
   """
   Returns full component breakdown for sanity checks:
   - goodwill (weighted revenue, factors, goodwill)
   - assets (leaseholds, equipment, supplies, total assets_only)
   - income (margin, growth, discount, dcf_value)
   - blending (asset_plus_goodwill, weights, final_value)
   """
   return compute_components(req)