"""Held-out capability/error-complementarity selection; no self-rated model scores."""
from itertools import combinations
import json
import math
from pathlib import Path

from agent.incident_replay import boundary


@boundary("apex.council_selection")
def select(profile, available, *, domain="general", size=3, token_cap=None):
    models = sorted(set(available))
    if token_cap is not None and (type(token_cap) not in (int, float) or not math.isfinite(token_cap) or token_cap < 0):
        raise ValueError("token cap must be finite and nonnegative")
    if type(size) is not int or not 2 <= size <= 4 or len(models) > 32:
        raise ValueError("select 2–4 members from at most 32 candidates")
    data = profile["domains"].get(domain)
    if not data or not data.get("source") or not data.get("heldout_ids"):
        return {"models": models[:size], "status": "uncalibrated_fallback"}
    ids = data["heldout_ids"]
    if len(ids) != len(set(ids)) or set(ids) & set(data.get("train_ids", [])):
        raise ValueError("profiling cases must be unique and held out from training")
    records = data["models"]
    eligible = []
    for model in models:
        if model not in records:
            continue
        row = records[model]
        if set(row["correct"]) != set(ids) or any(type(v) is not bool for v in row["correct"].values()):
            raise ValueError("every model needs boolean execution labels on the same held-out cases")
        cost = row["tokens_per_query"]
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise ValueError("finite measured token costs required")
        eligible.append(model)
    best = None
    for team in combinations(eligible, size):
        tokens = sum(records[m]["tokens_per_query"] for m in team)
        if token_cap is not None and tokens > token_cap:
            continue
        vectors = [[not records[m]["correct"][i] for i in ids] for m in team]
        accuracy = sum(sum(not e for e in v)/len(ids) for v in vectors)/size
        all_wrong = sum(all(v[j] for v in vectors) for j in range(len(ids)))/len(ids)
        covariances = []
        for a, b in combinations(vectors, 2):
            ea, eb = sum(a)/len(a), sum(b)/len(b)
            covariances.append(sum((x-ea)*(y-eb) for x,y in zip(a,b))/len(a))
        covariance = sum(covariances)/len(covariances)
        # Transparent heuristic, not the paper's learned selector or a gain claim.
        score = accuracy - all_wrong - max(0, covariance)
        key = (score, -tokens, tuple(team))
        if best is None or key > best[0]:
            best = (key, team, {"mean_accuracy": accuracy, "all_wrong_rate": all_wrong,
                               "mean_error_covariance": covariance, "tokens": tokens})
    if best is None:
        return {"models": [] if token_cap is not None else models[:size], "status": "insufficient_calibration"}
    return {"models": list(best[1]), "status": "calibrated", "source": data["source"], **best[2]}


def configured_panel(path, available, domain):
    profile = json.loads(Path(path).read_text())
    choice = select(profile, available, domain=domain, size=min(3, len(available)))
    return choice["models"] if choice["status"] == "calibrated" else None
