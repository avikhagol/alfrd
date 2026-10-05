"""Baseline report: N consecutive production agent turns, measured, never treated.

Selection follows production order from a starting boundary. Every attempt of
a logical turn counts, failed and retried ones included; only turns a person
excludes with outage evidence are skipped. Acceptance is the publication
proxy (the response passed validation and was published). Unknown counts stay
unknown: a category total with a missing attempt is partial, and a field
reported by fewer than ``coverage`` of the attempts is unusable for pilot
thresholds. Estimates (``est_usage``) are never read.
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

CATEGORIES = ("input_uncached_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
LABELS = {"input_uncached_tokens": "Uncached input", "cache_read_input_tokens": "Cache read",
          "cache_creation_input_tokens": "Cache creation", "output_tokens": "Output"}
REQUIRED = (*CATEGORIES, "outcome")
COVERAGE = 0.80
SPREAD_FLAG = 0.10


def _known(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def attempts(root: str | Path) -> list[dict[str, Any]]:
    """Every agent-turn attempt of every plan, in launch order."""
    from alfrd.agent_io import normalized_usage
    from alfrd.runtime.scheduler import PlanDir, list_plans

    root = Path(root).resolve()
    out = []
    for plan in list_plans(root):
        for unit in PlanDir(root, plan["id"]).units():
            if not unit.get("handoff"):
                continue
            out.append({**unit, "plan": unit.get("plan") or plan["id"], "agent_usage": normalized_usage(unit)})
    return sorted(out, key=lambda u: (str(u.get("started") or ""), str(u.get("plan")), str(u.get("id"))))


def _attempt_row(unit: Mapping[str, Any]) -> dict[str, Any]:
    usage = unit.get("agent_usage") or {}
    handoff = unit.get("handoff") or {}
    return {
        "address": f"{unit.get('plan')}/{unit.get('id')}", "attempt": unit.get("attempt_number"),
        "retry_of": unit.get("retry_of"), "usage_source": unit.get("usage_source"),
        "usage": {key: usage.get(key) if _known(usage.get(key)) else None for key in CATEGORIES},
        "raw_input_chars": handoff.get("raw_input_chars"), "trimmed_handoff_chars": handoff.get("trimmed_handoff_chars"),
        "final_prompt_chars": handoff.get("prompt_chars"), "sections_truncated": handoff.get("sections_truncated"),
        "status": unit.get("status"), "outcome": unit.get("outcome"), "reason": unit.get("outcome_reason") or unit.get("error"),
        "models": list(unit.get("models") or ([unit["model"]] if unit.get("model") else [])),
        "requested_model": unit.get("requested_model"),
        "hashes": {"input": handoff.get("input_sha256"), "prompt": handoff.get("prompt_sha256"),
                   "artifact": (unit.get("artifact") or {}).get("sha256")},
        "started": unit.get("started"),
    }


def select(units: Sequence[Mapping[str, Any]], *, start: str | None = None, count: int = 10,
           exclude: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Group attempts into logical turns and pick ``count`` eligible ones from ``start``."""
    exclude = dict(exclude or {})
    turns: dict[str, list[Mapping[str, Any]]] = {}
    skipped = {"unlabeled": 0, "not_baseline": 0}
    for unit in units:
        if not unit.get("logical_turn_id"):
            skipped["unlabeled"] += 1
            continue
        turns.setdefault(unit["logical_turn_id"], []).append(unit)
    ordered = list(turns.items())
    if start:
        index = next((i for i, (_tid, group) in enumerate(ordered)
                      if start in (_tid, *(f"{u.get('plan')}/{u.get('id')}" for u in group))), None)
        if index is None:
            raise ValueError(f"starting boundary {start!r} is not a recorded turn or attempt")
        ordered = ordered[index:]
    included, excluded = [], []
    for turn_id, group in ordered:
        if len(included) >= count:
            break
        if any(u.get("treatment") != "baseline" or u.get("run_kind") != "production" for u in group):
            skipped["not_baseline"] += 1
            continue
        if turn_id in exclude:
            excluded.append({"turn": turn_id, "evidence": exclude[turn_id],
                             "attempts": [f"{u.get('plan')}/{u.get('id')}" for u in group]})
            continue
        included.append((turn_id, group))
    unknown_exclusions = sorted(set(exclude) - {e["turn"] for e in excluded})
    return {"turns": included, "excluded": excluded, "skipped": skipped, "unmatched_exclusions": unknown_exclusions}


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None, "spread": None, "relative_spread": None, "mean": None, "flag": None}
    median = statistics.median(values)
    spread = max(values) - min(values)
    relative = spread / median if median else None
    return {"n": len(values), "median": median, "min": min(values), "max": max(values), "spread": spread,
            "relative_spread": relative, "mean": statistics.fmean(values),
            "flag": None if relative is None else relative > SPREAD_FLAG}


def report(root: str | Path, *, start: str | None = None, count: int = 10, exclude: Mapping[str, str] | None = None,
           coverage: float = COVERAGE, prices: Mapping[str, Any] | None = None) -> dict[str, Any]:
    picked = select(attempts(root), start=start, count=count, exclude=exclude)
    turn_rows = []
    all_attempts = []
    for position, (turn_id, group) in enumerate(picked["turns"], 1):
        rows = [_attempt_row(u) for u in group]
        all_attempts.extend(rows)
        totals, complete = {}, {}
        for key in CATEGORIES:
            values = [r["usage"][key] for r in rows]
            known = [v for v in values if v is not None]
            complete[key] = len(known) == len(values)
            totals[key] = sum(known) if known else None
        turn_rows.append({"position": position, "logical_turn_id": turn_id, "attempts": rows,
                          "models": sorted({m for r in rows for m in r["models"]}),
                          "usage": totals, "complete": complete,
                          "accepted": int(any(r["outcome"] == "accepted" for r in rows))})
    n_turns, n_attempts = len(turn_rows), len(all_attempts)
    accepted = sum(t["accepted"] for t in turn_rows)

    def field_coverage(getter) -> dict[str, Any]:
        known = sum(1 for r in all_attempts if getter(r) is not None)
        share = known / n_attempts if n_attempts else 0.0
        return {"known": known, "total": n_attempts, "coverage": share, "usable": n_attempts > 0 and share >= coverage}

    coverage_by = {key: field_coverage(lambda r, k=key: r["usage"][k]) for key in CATEGORIES}
    coverage_by["outcome"] = field_coverage(lambda r: r["outcome"])
    coverage_by["raw_input_chars"] = field_coverage(lambda r: r["raw_input_chars"])
    coverage_by["sections_truncated"] = field_coverage(lambda r: r["sections_truncated"])

    categories = {}
    for key in CATEGORIES:
        known = [r["usage"][key] for r in all_attempts if r["usage"][key] is not None]
        subtotal = sum(known) if known else None
        partial = len(known) < n_attempts
        complete_totals = [t["usage"][key] for t in turn_rows if t["complete"][key] and t["usage"][key] is not None]
        per_accepted = None if not accepted or subtotal is None else subtotal / accepted
        categories[key] = {**coverage_by[key], "unknown": n_attempts - len(known), "known_subtotal": subtotal,
                           "per_accepted": per_accepted, "per_accepted_partial": partial and per_accepted is not None,
                           "complete_turns": len(complete_totals), **_stats([float(v) for v in complete_totals])}
    truncation_known = [r for r in all_attempts if r["sections_truncated"] is not None]
    reasons = []
    if n_turns < count:
        reasons.append(f"only {n_turns} of {count} eligible logical turns are recorded")
    for key in REQUIRED:
        if not coverage_by[key]["usable"]:
            reasons.append(f"{LABELS.get(key, key)}: coverage {coverage_by[key]['coverage']:.0%} is below {coverage:.0%}")
    return {
        "schema": "alfrd.baseline/1", "start": start, "count": count, "coverage_bar": coverage,
        "acceptance": "passed validation and published", "prices": dict(prices) if prices else None,
        "turns": turn_rows, "excluded": picked["excluded"], "skipped": picked["skipped"],
        "unmatched_exclusions": picked["unmatched_exclusions"],
        "summary": {
            "turns": n_turns, "attempts": n_attempts, "accepted": accepted,
            "acceptance_rate": accepted / count if count else None,
            "retry_rate": sum(1 for t in turn_rows if len(t["attempts"]) > 1) / count if count else None,
            "extra_attempts": sum(len(t["attempts"]) - 1 for t in turn_rows),
            "truncated_attempts": sum(1 for r in truncation_known if r["sections_truncated"]),
            "truncation_known": len(truncation_known), "truncation_unknown": n_attempts - len(truncation_known),
        },
        "coverage": coverage_by, "categories": categories,
        "readiness": {"ready": not reasons, "reasons": reasons},
    }


def _n(value: Any, digits: int = 0) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and digits:
        return f"{value:.{digits}f}"
    return f"{round(value):,}" if isinstance(value, (int, float)) else str(value)


def markdown(doc: Mapping[str, Any]) -> str:
    s = doc["summary"]
    lines = ["# Baseline report", "",
             f"- Starting boundary: {doc['start'] or 'first recorded turn'}",
             f"- Logical turns: {s['turns']} of {doc['count']} · attempts {s['attempts']} · accepted {s['accepted']}",
             f"- Acceptance: {doc['acceptance']} (publication proxy, not correctness)",
             f"- Coverage bar: {doc['coverage_bar']:.0%} of attempts per field",
             f"- Monetary cost: {'see price table' if doc.get('prices') else 'not computed'}",
             f"- Readiness: **{'pass' if doc['readiness']['ready'] else 'failed'}**"]
    lines += [f"  - {reason}" for reason in doc["readiness"]["reasons"]]
    lines += ["", "## Turns", "", "| # | Logical turn | Attempts | Models | Uncached input | Cache read | Cache creation | Output | Accepted | Complete |",
              "|---:|---|---:|---|---:|---:|---:|---:|---:|---|"]
    for t in doc["turns"]:
        cells = [(_n(t["usage"][k]) + ("" if t["complete"][k] else " (partial)")) for k in CATEGORIES]
        lines.append(f"| {t['position']} | `{t['logical_turn_id'][:12]}` | {len(t['attempts'])} | {', '.join(t['models']) or '—'} | "
                     + " | ".join(cells) + f" | {t['accepted']} | {'yes' if all(t['complete'].values()) else 'no'} |")
    lines += ["", "## Attempts", "", "| Turn | Attempt / address / retry of | Usage source | Uncached / read / creation / output | Raw / trimmed / final chars | Truncated | Status / outcome / reason |",
              "|---:|---|---|---|---|---|---|"]
    for t in doc["turns"]:
        for r in t["attempts"]:
            usage = " / ".join(_n(r["usage"][k]) for k in CATEGORIES)
            sizes = " / ".join(_n(r[k]) for k in ("raw_input_chars", "trimmed_handoff_chars", "final_prompt_chars"))
            truncated = "unknown" if r["sections_truncated"] is None else ", ".join(r["sections_truncated"]) or "none"
            reason = str(r["reason"] or "").replace("|", "\\|").replace("\n", " ")[:80]
            lines.append(f"| {t['position']} | {r['attempt'] or '?'} · `{r['address']}` · {r['retry_of'] or '—'} | {r['usage_source'] or '—'} | "
                         f"{usage} | {sizes} | {truncated} | {r['status']} / {r['outcome'] or '—'} / {reason or '—'} |")
    lines += ["", "## Summary", "",
              f"- Acceptance rate: {_n(s['acceptance_rate'], 2)} (accepted logical turns ÷ {doc['count']})",
              f"- Retry rate: {_n(s['retry_rate'], 2)} · extra attempts: {s['extra_attempts']}",
              f"- Truncation: {s['truncated_attempts']} of {s['truncation_known']} attempts with known state; unknown {s['truncation_unknown']}",
              "- Exclusions: " + ("; ".join(f"`{e['turn']}` — {e['evidence']}" for e in doc["excluded"]) or "none"),
              f"- Skipped as unlabeled (recorded before lineage) / not baseline: {doc['skipped']['unlabeled']} / {doc['skipped']['not_baseline']}"]
    if doc["unmatched_exclusions"]:
        lines.append(f"- Exclusions that matched no selected turn: {', '.join(doc['unmatched_exclusions'])}")
    lines += ["", "## Usage categories", "",
              "| Category | Known / attempts | Coverage | Unknown | Known subtotal | Per accepted result | Median | Min | Max | Spread | Relative spread | Mean | Complete turns |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key, c in doc["categories"].items():
        per = "undefined" if c["per_accepted"] is None and doc["summary"]["accepted"] == 0 else _n(c["per_accepted"]) + (" (partial)" if c["per_accepted_partial"] else "")
        rel = "undefined" if c["relative_spread"] is None and c["n"] else _n(c["relative_spread"], 2) + (" ⚑" if c["flag"] else "")
        lines.append(f"| {LABELS[key]}{'' if c['usable'] else ' (unusable)'} | {c['known']} / {c['total']} | {c['coverage']:.0%} | {c['unknown']} | "
                     f"{_n(c['known_subtotal'])} | {per} | {_n(c['median'])} | {_n(c['min'])} | {_n(c['max'])} | {_n(c['spread'])} | {rel} | {_n(c['mean'])} | {c['complete_turns']} |")
    lines += ["", "Statistics use complete logical-turn totals only. ⚑ marks relative spread above 10%: "
              "plan larger pilot samples or matched replays (not a power calculation).", ""]
    return "\n".join(lines)
