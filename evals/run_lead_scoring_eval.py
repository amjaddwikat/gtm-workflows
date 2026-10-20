#!/usr/bin/env python3
"""Evaluate the inbound lead scorer against a labelled lead set.

Requests are built, and responses parsed, by executing the scorer
workflow's own Code nodes ("Build Scoring Request", "Parse Score"), so
the eval measures exactly what production runs. Only the HTTP call in
between is made here, through the official Anthropic SDK.

    pip install anthropic
    export ANTHROPIC_API_KEY=...
    python evals/run_lead_scoring_eval.py                 # live run (~32 calls, ~$0.01)
    python evals/run_lead_scoring_eval.py --self-check    # no API: validates labels vs scoring code

Exit code 1 when tier accuracy is below --min-accuracy or any severe
error occurs (an A-tier lead scored C/DQ, or a C/DQ lead scored A).
"""
import argparse
import json
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCORER = ROOT / "workflows" / "inbound-lead-scorer.n8n.json"
DATASET = ROOT / "evals" / "lead_scoring" / "leads.jsonl"
FIELDS = ("company_fit", "persona", "disqualifier")
LOW, HIGH = {"C", "DQ"}, {"A"}


def run_node(node_name, fixture):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(fixture, fh)
    try:
        out = subprocess.run(["node", str(ROOT / "tests" / "run_code_node.js"), str(SCORER), node_name, fh.name],
                             capture_output=True, text=True, encoding="utf-8", check=True)
    finally:
        Path(fh.name).unlink()
    result = json.loads(out.stdout)
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"{node_name}: {result['error']}")
    return [item["json"] for item in result]


def as_contact(lead):
    return {"vid": 0, "properties": {k: {"value": v} for k, v in lead.items()}}


def call_claude(requests):
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY; retries 429/5xx twice by default
    responses, latencies = [], []
    for i, req in enumerate(requests, 1):
        start = time.perf_counter()
        try:
            msg = client.messages.create(**req)
            responses.append(msg.model_dump(mode="json"))
        except anthropic.RateLimitError as e:
            responses.append({"stop_reason": "api_error", "content": [], "error": f"rate limited: {e}"})
        except anthropic.APIStatusError as e:
            responses.append({"stop_reason": "api_error", "content": [], "error": f"{e.status_code}: {e.message}"})
        except anthropic.APIConnectionError as e:
            responses.append({"stop_reason": "api_error", "content": [], "error": f"connection: {e}"})
        latencies.append(time.perf_counter() - start)
        print(f"\r  scored {i}/{len(requests)}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    return responses, latencies


def perfect_responses(rows):
    """What a model that agrees with every label would return (for --self-check)."""
    return [{"stop_reason": "end_turn", "usage": {},
             "content": [{"type": "text", "text": json.dumps(
                 {**{f: r["expect"][f] for f in FIELDS}, "confidence": "high", "reasons": [r["note"]]})}]}
            for r in rows]


def report(rows, results, latencies):
    n = len(rows)
    tier_ok = sum(r["expect"]["tier"] == s["tier"] for r, s in zip(rows, results))
    severe = [(r["id"], r["expect"]["tier"], s["tier"]) for r, s in zip(rows, results)
              if (r["expect"]["tier"] in HIGH and s["tier"] in LOW) or (r["expect"]["tier"] in LOW and s["tier"] in HIGH)]
    unscored = [r["id"] for r, s in zip(rows, results) if s["tier"] == "unscored"]
    field_acc = {f: sum(r["expect"][f] == s.get(f) for r, s in zip(rows, results)) / n for f in FIELDS}

    exp_dq = {r["id"] for r in rows if r["expect"]["tier"] == "DQ"}
    got_dq = {r["id"] for r, s in zip(rows, results) if s["tier"] == "DQ"}
    precision = len(exp_dq & got_dq) / len(got_dq) if got_dq else 1.0
    recall = len(exp_dq & got_dq) / len(exp_dq) if exp_dq else 1.0

    labels = ["A", "B", "C", "DQ", "unscored"]
    confusion = Counter((r["expect"]["tier"], s["tier"]) for r, s in zip(rows, results))
    cost = sum(s.get("cost_usd") or 0 for s in results)

    print(f"\nTier accuracy: {tier_ok}/{n} = {tier_ok / n:.0%}")
    print("Field accuracy: " + ", ".join(f"{f} {a:.0%}" for f, a in field_acc.items()))
    print(f"Disqualifier precision {precision:.0%}, recall {recall:.0%}")
    print(f"Severe errors: {len(severe)} {severe if severe else ''}")
    print(f"Unscored: {len(unscored)} {unscored if unscored else ''}")
    if latencies:
        print(f"Cost: ${cost:.4f} total, ${cost / n:.5f} per lead | "
              f"latency p50 {statistics.median(latencies):.1f}s, max {max(latencies):.1f}s")
    print("\nConfusion (rows = expected, cols = predicted)")
    print("        " + "".join(f"{lab:>9}" for lab in labels))
    for e in labels[:4]:
        print(f"{e:>8}" + "".join(f"{confusion.get((e, p), 0):>9}" for p in labels))
    misses = [(r["id"], r["expect"]["tier"], s["tier"], s.get("reasons") or s.get("error"))
              for r, s in zip(rows, results) if r["expect"]["tier"] != s["tier"]]
    if misses:
        print("\nMisses:")
        for m in misses:
            print(f"  {m[0]}: expected {m[1]}, got {m[2]} ({m[3]})")
    return {"n": n, "tier_accuracy": tier_ok / n, "field_accuracy": field_acc,
            "dq_precision": precision, "dq_recall": recall, "severe_errors": severe,
            "unscored": unscored, "cost_usd": cost, "misses": misses}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true", help="no API calls; check labels against scoring code")
    ap.add_argument("--min-accuracy", type=float, default=0.80)
    ap.add_argument("--out", type=Path, help="write the metrics as JSON")
    args = ap.parse_args()

    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    built = run_node("Build Scoring Request", {"input": [as_contact(r["lead"]) for r in rows]})

    if args.self_check:
        responses, latencies = perfect_responses(rows), []
    else:
        print(f"Scoring {len(rows)} leads with {built[0]['request']['model']}...", file=sys.stderr)
        responses, latencies = call_claude([b["request"] for b in built])

    results = run_node("Parse Score", {"input": responses, "nodes": {"Build Scoring Request": built}})
    metrics = report(rows, results, latencies)
    if args.out:
        args.out.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    threshold = 1.0 if args.self_check else args.min_accuracy
    ok = metrics["tier_accuracy"] >= threshold and not metrics["severe_errors"]
    print(f"\n{'PASS' if ok else 'FAIL'} (tier accuracy >= {threshold:.0%}, no severe errors)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
