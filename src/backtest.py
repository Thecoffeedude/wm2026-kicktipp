"""
Replay the snapshot store through the tip pipeline to validate calibration
choices (κ, ρ, γ, source weights) against the realised Kicktipp points.

Reconstructs, per settled match, the exact tip build_data would have produced:
  - blend path when both uanalyse and closing odds exist,
  - uanalyse-only (λ calibrated to its own 1X2) otherwise,
  - odds-only when uanalyse is missing.
Then scores it with config.kicktipp_points against the real result.

Run: python3 -m src.backtest      (prints a config comparison table)
"""

from __future__ import annotations

import argparse

import config
from src import snapshot_store as ss
from src import weighting
from src.calibration import apply_kappa
from src.scoreline import (_lambda_from_totals, derive_xg, ev_optimize,
                           poisson_matrix)


def _collect(events: list[dict]) -> dict[str, dict]:
    """match_id → {ua, odds(closing), result}; closing = latest captured odds."""
    by: dict[str, dict] = {}
    for e in events:
        mid = e.get("match_id")
        if not mid:
            continue
        slot = by.setdefault(mid, {})
        t = e.get("type")
        if t == "result":
            slot["result"] = e
        elif t == "uanalyse":
            slot["ua"] = e
        elif t == "odds":
            prev = slot.get("odds")
            if prev is None or e.get("captured_at", "") >= prev.get("captured_at", ""):
                slot["odds"] = e
    return by


def tip_for_match(slot: dict, kappa: float, rho: float, gamma: float,
                  weights: dict) -> tuple[tuple[int, int], str] | None:
    """Reconstruct the recommended (home, away) tip and the path used."""
    ua = slot.get("ua")
    od = slot.get("odds")

    if ua and od:
        ua_p = ua["p"]
        market_p = od["p"]
        blended = weighting.logit_pool(
            [ua_p, market_p], [weights["uanalyse"], weights["market"]]
        )
        blam = weighting.calibrate_lambda(
            blended, od.get("totals_line"), od.get("totals_over_prob"),
            lambda_total_hint=ua["lambda"]["home"] + ua["lambda"]["away"],
        )
        lh, la = apply_kappa(blam["home"], blam["away"], kappa)
        path = "blend"
    elif ua:
        ua_p = ua["p"]
        lam = weighting.calibrate_lambda(
            ua_p, None, None,
            lambda_total_hint=ua["lambda"]["home"] + ua["lambda"]["away"],
        )
        lh, la = apply_kappa(lam["home"], lam["away"], kappa)
        path = "uanalyse"
    elif od:
        xg = derive_xg(od["p"], od.get("totals_line"), od.get("totals_over_prob"))
        lh, la = apply_kappa(xg["home"], xg["away"], kappa)
        path = "odds"
    else:
        return None

    matrix = poisson_matrix(lh, la, rho=rho)
    tip, _ = ev_optimize(matrix, variance_aggression=gamma)
    return (tip["home"], tip["away"]), path


def run(kappa: float, rho: float, gamma: float,
        weights: dict | None = None, verbose: bool = False) -> dict:
    """Total Kicktipp points over all settled matches for one config."""
    weights = weights or dict(weighting.PRIOR_SHARP)
    events = ss.load_events()
    by = _collect(events)

    total = 0
    n = 0
    draws_tipped = 0
    exact = 0
    rows = []
    for mid, slot in sorted(by.items()):
        res = slot.get("result")
        if not res:
            continue
        out = tip_for_match(slot, kappa, rho, gamma, weights)
        if out is None:
            continue
        (th, ta), path = out
        real = (res["score_home"], res["score_away"])
        pts = config.kicktipp_points((th, ta), real)
        total += pts
        n += 1
        draws_tipped += int(th == ta)
        exact += int((th, ta) == real)
        rows.append((mid, f"{th}:{ta}", f"{real[0]}:{real[1]}", pts, path))

    if verbose:
        for mid, tip, real, pts, path in rows:
            print(f"  {mid:28s} tip {tip}  real {real}  {pts} pts  [{path}]")

    return {
        "kappa": kappa, "rho": rho, "gamma": gamma,
        "n": n, "points": total, "ppg": round(total / n, 3) if n else 0.0,
        "draws_tipped": draws_tipped, "exact": exact,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Calibration backtest over the snapshot store")
    ap.add_argument("--verbose", action="store_true", help="print per-match rows for the live config")
    args = ap.parse_args()

    from src import calibration
    events = ss.load_events()
    live_kappa, _ = calibration.resolve_kappa(events)
    live_rho = calibration.rho_value()
    live_gamma = calibration.variance_value()

    print("Backtest over the snapshot store (PRIOR_SHARP weights):\n")
    print(f"{'config':36s} {'n':>3} {'pts':>5} {'ppg':>6} {'draws':>6} {'exact':>6}")
    grid = [
        ("baseline κ1.00 ρ0.00 γ0.0", 1.00, 0.0, 0.0),
        ("κ1.15 ρ0.00 γ0.0",          1.15, 0.0, 0.0),
        ("κ1.15 ρ-0.10 γ0.0",         1.15, -0.10, 0.0),
        ("κ1.25 ρ-0.10 γ0.0",         1.25, -0.10, 0.0),
        (f"LIVE κ{live_kappa:.2f} ρ{live_rho:.2f} γ{live_gamma:.1f}",
         live_kappa, live_rho, live_gamma),
        ("κ1.15 ρ-0.10 γ0.5",         1.15, -0.10, 0.5),
        ("κ1.15 ρ-0.10 γ1.0",         1.15, -0.10, 1.0),
    ]
    for label, k, r, g in grid:
        res = run(k, r, g)
        print(f"{label:36s} {res['n']:>3} {res['points']:>5} {res['ppg']:>6} "
              f"{res['draws_tipped']:>6} {res['exact']:>6}")

    if args.verbose:
        print("\nLIVE config per-match:")
        run(live_kappa, live_rho, live_gamma, verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
