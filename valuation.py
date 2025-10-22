from dataclasses import dataclass
from typing import List

@dataclass
class Inputs:
    collections_2024: float
    collections_2025: float
    equipment_value: float = 0.0
    leasehold_value: float = 0.0
    supplies_value: float = 0.0
    benchmark_pct: float = 0.95
    adjustment_pct: float = 0.02
    goodwill_pct: float = 0.97
    margin_pct: float = 0.20
    growth_pct: float = 0.05
    years: int = 5
    discount_rate: float = 0.20
    terminal_rev_pct: float = 0.80

@dataclass
class Outputs:
    weighted_revenue: float
    goodwill: float
    tangible_assets: float
    dcf_value: float
    asset_value_total: float
    final_value: float
    rationale: List[str]

def weighted_revenue(c24: float, c25: float) -> float:
    return (c25 * 3 + c24 * 2) / 5.0

def goodwill_from_weighted(wr: float, benchmark_pct: float, adjustment_pct: float, goodwill_pct: float):
    base = wr * benchmark_pct
    adj_value = base * (1 + adjustment_pct)
    goodwill = goodwill_pct * wr
    return goodwill, base, adj_value

def simple_dcf(start_collections: float, margin_pct: float, growth_pct: float, years: int, discount_rate: float, terminal_rev_pct: float) -> float:
    pv = 0.0
    collections = start_collections
    for t in range(1, years + 1):
        ocf = collections * margin_pct
        pv += ocf / ((1 + discount_rate) ** t)
        collections *= (1 + growth_pct)
    terminal_value = terminal_rev_pct * collections
    pv += terminal_value / ((1 + discount_rate) ** years)
    return pv

def compute(inputs: Inputs) -> Outputs:
    notes = []
    wr = weighted_revenue(inputs.collections_2024, inputs.collections_2025)
    notes.append(f"Weighted revenue = (C25×3 + C24×2)/5 = {wr:,.0f}")

    goodwill, base_benchmark, adjusted_benchmark = goodwill_from_weighted(
        wr, inputs.benchmark_pct, inputs.adjustment_pct, inputs.goodwill_pct
    )
    notes.append(f"Benchmark @ {inputs.benchmark_pct*100:.0f}% of WR = {base_benchmark:,.0f}; + adjustments {inputs.adjustment_pct*100:.1f}% → {adjusted_benchmark:,.0f}")
    notes.append(f"Goodwill (broker-style) = {inputs.goodwill_pct*100:.0f}% of WR = {goodwill:,.0f}")

    tangible = inputs.equipment_value + inputs.leasehold_value + inputs.supplies_value
    notes.append(f"Tangible assets = equipment + leasehold + supplies = {tangible:,.0f}")

    dcf = simple_dcf(
        start_collections=inputs.collections_2025,
        margin_pct=inputs.margin_pct,
        growth_pct=inputs.growth_pct,
        years=inputs.years,
        discount_rate=inputs.discount_rate,
        terminal_rev_pct=inputs.terminal_rev_pct
    )
    notes.append(f"DCF (margin {inputs.margin_pct*100:.0f}%, growth {inputs.growth_pct*100:.0f}%, discount {inputs.discount_rate*100:.0f}%) = {dcf:,.0f}")

    asset_total = tangible + goodwill
    final_value = (dcf + asset_total) / 2.0
    notes.append("Final valuation = average of (DCF, Tangible+Goodwill)")

    return Outputs(
        weighted_revenue=wr,
        goodwill=goodwill,
        tangible_assets=tangible,
        dcf_value=dcf,
        asset_value_total=asset_total,
        final_value=final_value,
        rationale=notes
    )
