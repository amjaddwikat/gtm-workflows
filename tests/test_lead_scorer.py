"""Offline tests for the Claude lead scorer (no API calls).

The request builder and response parser are the scorer's Code nodes,
executed in Node.js against fixtures. Live model quality is measured
separately by evals/run_lead_scoring_eval.py.

Run:  python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCORER = ROOT / "workflows" / "inbound-lead-scorer.n8n.json"
INBOUND = ROOT / "workflows" / "inbound-speed-to-lead.n8n.json"


def run_node(workflow, node_name, fixture):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(fixture, fh)
    try:
        out = subprocess.run(["node", str(ROOT / "tests" / "run_code_node.js"), str(workflow), node_name, fh.name],
                             capture_output=True, text=True, encoding="utf-8", check=True)
    finally:
        Path(fh.name).unlink()
    return json.loads(out.stdout)


def contact(**props):
    return {"vid": 1, "properties": {k: {"value": v} for k, v in props.items()}}


LEAD = contact(firstname="Dana", lastname="Reyes", work_email="dana@brightpath.example",
               jobtitle="VP of Revenue Operations", company="Brightpath Analytics",
               numberofemployees="240", industry="Computer Software")


def response(judgement, *, stop="end_turn", thinking=False, usage=None):
    content = [{"type": "thinking", "thinking": "", "signature": "x"}] if thinking else []
    content.append({"type": "text", "text": judgement if isinstance(judgement, str) else json.dumps(judgement)})
    return {"stop_reason": stop, "content": content,
            "usage": usage or {"input_tokens": 120, "output_tokens": 80, "cache_read_input_tokens": 900}}


def judgement(company_fit="strong", persona="economic_buyer", disqualifier="none", reasons=None):
    return {"company_fit": company_fit, "persona": persona, "disqualifier": disqualifier,
            "confidence": "high", "reasons": reasons or ["VP RevOps", "240-person SaaS"]}


@unittest.skipUnless(shutil.which("node") or os.environ.get("CI"), "Node.js not installed")
class BuildScoringRequest(unittest.TestCase):
    def build(self, c=LEAD):
        return run_node(SCORER, "Build Scoring Request", {"input": [c]})[0]["json"]

    def test_request_shape_matches_haiku_5_5_rules(self):
        req = self.build()["request"]
        self.assertEqual(req["model"], "claude-haiku-5-5")
        for banned in ("temperature", "top_p", "top_k"):        # non-default sampling params 400 on Haiku 5.5
            self.assertNotIn(banned, req)
        self.assertGreaterEqual(req["max_tokens"], 1024)        # adaptive thinking counts toward max_tokens
        self.assertEqual(req["output_config"]["effort"], "low")
        self.assertEqual(req["messages"][-1]["role"], "user")   # no assistant prefill
        self.assertEqual(req["system"][0]["cache_control"], {"type": "ephemeral"})

    def test_schema_is_strict(self):
        schema = self.build()["request"]["output_config"]["format"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))

    def test_sends_only_what_the_rubric_needs(self):
        out = self.build()
        self.assertEqual(out["lead"], {"job_title": "VP of Revenue Operations", "company": "Brightpath Analytics",
                                       "email_domain": "brightpath.example", "employees": "240",
                                       "industry": "Computer Software"})
        msg = out["request"]["messages"][0]["content"]
        self.assertNotIn("Dana", msg)                            # no name
        self.assertNotIn("dana@", msg)                           # no full email address
        self.assertIn("<lead>", msg)                             # untrusted data is fenced

    def test_missing_fields_become_empty(self):
        lead = self.build(contact(jobtitle="Head of Sales"))["lead"]
        self.assertEqual(lead["company"], "")
        self.assertEqual(lead["email_domain"], "")


@unittest.skipUnless(shutil.which("node") or os.environ.get("CI"), "Node.js not installed")
class ParseScore(unittest.TestCase):
    def parse(self, resp, lead=None):
        lead = lead or {"email_domain": "brightpath.example"}
        fx = {"input": [resp], "nodes": {"Build Scoring Request": [{"lead": lead}]}}
        return run_node(SCORER, "Parse Score", fx)[0]["json"]

    def test_points_and_tiers_are_computed_in_code(self):
        cases = [(("strong", "economic_buyer"), 100, "A"), (("strong", "champion"), 85, "A"),
                 (("partial", "economic_buyer"), 80, "A"), (("partial", "champion"), 65, "B"),
                 (("unknown", "champion"), 55, "B"), (("weak", "economic_buyer"), 60, "B"),
                 (("unknown", "user"), 35, "C"), (("weak", "none"), 10, "C")]
        for (fit, persona), score, tier in cases:
            r = self.parse(response(judgement(fit, persona)))
            self.assertEqual((r["score"], r["tier"]), (score, tier), (fit, persona))

    def test_reads_text_block_after_thinking(self):
        self.assertEqual(self.parse(response(judgement(), thinking=True))["tier"], "A")

    def test_disqualifier_overrides_fit(self):
        r = self.parse(response(judgement(disqualifier="competitor")))
        self.assertEqual((r["tier"], r["score"]), ("DQ", 0))
        self.assertIn("competitor", r["summary"])

    def test_free_mail_is_enforced_even_if_model_misses_it(self):
        r = self.parse(response(judgement()), lead={"email_domain": "gmail.com"})
        self.assertEqual((r["tier"], r["disqualifier"]), ("DQ", "personal_email"))

    def test_refusal_and_truncation_are_unscored(self):
        for stop in ("refusal", "max_tokens"):
            r = self.parse(response(judgement(), stop=stop))
            self.assertEqual(r["tier"], "unscored", stop)
            self.assertIn(stop, r["error"])

    def test_invalid_output_is_unscored(self):
        self.assertEqual(self.parse(response("not json"))["tier"], "unscored")
        self.assertEqual(self.parse(response(judgement(company_fit="excellent")))["tier"], "unscored")

    def test_reasons_capped_and_summary_readable(self):
        r = self.parse(response(judgement(reasons=["a", "b", "c", "d"])))
        self.assertEqual(r["reasons"], ["a", "b", "c"])
        self.assertTrue(r["summary"].startswith("🔥 Fit: A-tier (100/100): a; b; c"))

    def test_cost_uses_haiku_5_5_prices(self):
        r = self.parse(response(judgement(), usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000}))
        self.assertAlmostEqual(r["cost_usd"], 0.60)

    def test_fallback_passes_lead_on_unscored(self):
        out = run_node(SCORER, "Unscored Fallback", {"input": [{"error": {"message": "429 rate limited"}}]})
        self.assertEqual(out[0]["json"]["tier"], "unscored")
        self.assertIn("429", out[0]["json"]["error"])


class InboundWiring(unittest.TestCase):
    def setUp(self):
        self.wf = json.loads(INBOUND.read_text(encoding="utf-8"))
        self.conn = self.wf["connections"]
        self.nodes = {n["name"]: n for n in self.wf["nodes"]}

    def targets(self, src, out=0):
        return [e["node"] for e in self.conn[src]["main"][out]]

    def test_both_paths_are_scored_before_routing(self):
        self.assertEqual(self.targets("HubSpot1"), ["Score Lead (Owned)"])
        self.assertEqual(self.targets("Score Lead (Owned)"), ["IF Owner was assigned over an hour ago?"])
        self.assertEqual(self.targets("HubSpot"), ["Score Lead (Unowned)"])
        self.assertEqual(set(self.targets("Score Lead (Unowned)")), {"Switch", "IF Unowned A-tier"})

    def test_unowned_a_tier_leads_alert_a_human(self):
        self.assertEqual(self.targets("IF Unowned A-tier"), ["Alert: Unowned A-tier Lead"])

    def test_rep_alerts_and_clay_carry_the_score(self):
        for name in ("FT Notify Contact Owner", "FT Notify Contact SDR Owner",
                     "WP Notify Contact Owner", "WP Notify SDR Owner"):
            self.assertIn("Score Lead (Owned)').first().json.summary", self.nodes[name]["parameters"]["text"])
        for name in ("FT Send to Clay for Enrichment", "WP Send to Clay for Enrichment1",
                     "FT Send to Clay for Enrichment W/Owner", "WP Send to Clay for Enrichment W/Owner"):
            fields = {p["name"] for p in self.nodes[name]["parameters"]["bodyParameters"]["parameters"]}
            self.assertTrue({"fit_tier", "fit_score", "fit_summary"} <= fields, name)


if __name__ == "__main__":
    unittest.main()
