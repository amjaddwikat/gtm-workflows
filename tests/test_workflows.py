"""Behavioural tests for the exported workflows.

n8n can't run in CI here, so these tests check the parts that decide
business outcomes:

  * graph invariants: every router output is wired, no dead nodes, no
    unbounded loops
  * condition semantics: the bot-open filter is evaluated the way n8n
    evaluates it, against fast and slow opens
  * Code nodes: their JavaScript is executed in Node.js with fixtures

Run:  python -m unittest discover -s tests -v
"""
import hashlib
import hmac
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / "workflows"
N8N_FILES = sorted(WF.glob("*.n8n.json"))
TRIGGERS = ("trigger", "webhook", "emailreadimap")


def load(name):
    return json.loads((WF / name).read_text(encoding="utf-8"))


def node(wf, name):
    return next(n for n in wf["nodes"] if n["name"] == name)


def adjacency(wf):
    adj = {}
    for src, outs in wf["connections"].items():
        for i, branch in enumerate(outs.get("main", [])):
            for e in branch or []:
                adj.setdefault(src, []).append((i, e["node"]))
    return adj


def reachable(wf):
    adj = adjacency(wf)
    start = [n["name"] for n in wf["nodes"] if any(t in n["type"].lower() for t in TRIGGERS)]
    seen, stack = set(start), list(start)
    while stack:
        for _, nxt in adj.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def cycles(wf):
    """Simple cycles in the node graph (small graphs, plain DFS is fine)."""
    adj = {k: sorted({t for _, t in v}) for k, v in adjacency(wf).items()}
    found = set()

    def dfs(start, cur, path):
        for nxt in adj.get(cur, []):
            if nxt == start:
                found.add(tuple(sorted(path)))
            elif nxt not in path and nxt > start:
                dfs(start, nxt, path + [nxt])

    for n in adj:
        dfs(n, n, [n])
    return [list(c) for c in found]


# n8n dateTime operators: (left, right) -> bool
DATE_OPS = {
    "after": lambda a, b: a > b,
    "before": lambda a, b: a < b,
    "afterOrEquals": lambda a, b: a >= b,
    "beforeOrEquals": lambda a, b: a <= b,
}


class GraphInvariants(unittest.TestCase):
    def test_no_dead_nodes(self):
        allowed = {  # regional variant: click hand-off intentionally left to the main router
            "outbound-email-signal-router-regional.n8n.json": {
                "Send to Clay>SDRs(Link Clicked)", "Send Click to Make>HubSpot"},
        }
        for f in N8N_FILES:
            wf = load(f.name)
            dead = {n["name"] for n in wf["nodes"]
                    if "stickyNote" not in n["type"]} - reachable(wf) - allowed.get(f.name, set())
            self.assertFalse(dead, f"{f.name}: unreachable nodes {sorted(dead)}")

    def test_every_event_router_output_is_wired(self):
        for f in ["outbound-email-signal-router.n8n.json",
                  "outbound-email-signal-router-regional.n8n.json",
                  "linkedin-event-outreach-router.n8n.json"]:
            wf = load(f)
            outs = wf["connections"]["Switch"]["main"]
            keys = [r["outputKey"] for r in node(wf, "Switch")["parameters"]["rules"]["values"]]
            for key, branch in zip(keys + ["fallback", "error"], outs):
                self.assertTrue(branch, f"{f}: router output '{key}' is not connected")

    def test_no_unbounded_loops(self):
        """Every cycle must be bounded: a batch loop over a finite item list,
        or an IF that checks $runIndex."""
        for f in N8N_FILES:
            wf = load(f.name)
            by_name = {n["name"]: n for n in wf["nodes"]}
            for cyc in cycles(wf):
                bounded = any(
                    by_name[n]["type"].endswith("splitInBatches")
                    or (by_name[n]["type"].endswith(".if") and "$runIndex" in json.dumps(by_name[n]["parameters"]))
                    for n in cyc)
                self.assertTrue(bounded, f"{f.name}: unbounded loop through {cyc}")

    def test_failed_steps_alert_instead_of_looping(self):
        for f in N8N_FILES:
            wf = load(f.name)
            for n in wf["nodes"]:
                if n.get("onError") != "continueErrorOutput" or n["type"].endswith("switch"):
                    continue
                outs = wf["connections"].get(n["name"], {"main": []})["main"]
                if len(outs) > 1 and outs[1]:
                    self.assertTrue(n.get("retryOnFail") and n.get("maxTries", 3) <= 5,
                                    f"{f.name}: {n['name']} has no bounded retry")
                    targets = [e["node"] for e in outs[1]]
                    self.assertEqual(targets, ["Step Failed Alert"], f"{f.name}: {n['name']} error output")


class BotOpenFilter(unittest.TestCase):
    """Corporate security scanners 'open' every email within seconds of
    delivery. Only opens arriving more than 20s after the send are human."""

    def setUp(self):
        wf = load("outbound-email-signal-router.n8n.json")
        self.cond = node(wf, "IF Not an Automated Open?")["parameters"]["conditions"]["conditions"][0]
        self.window = int(re.search(r"seconds:\s*(\d+)", self.cond["leftValue"]).group(1))
        self.passes_to = [e["node"] for e in wf["connections"]["IF Not an Automated Open?"]["main"][0]]

    def is_recorded(self, seconds_after_send):
        sent = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
        now = sent + timedelta(seconds=seconds_after_send)            # $now when the open arrives
        left = sent + timedelta(seconds=self.window)                  # send time + window
        return DATE_OPS[self.cond["operator"]["operation"]](left, now)

    def test_scanner_open_is_ignored(self):
        self.assertFalse(self.is_recorded(3))

    def test_human_open_is_recorded(self):
        self.assertTrue(self.is_recorded(45 * 60))

    def test_true_branch_records_the_open(self):
        self.assertTrue(self.passes_to, "true branch must lead to open tracking")


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class CodeNodes(unittest.TestCase):
    def run_node(self, workflow, name, fixture):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(fixture, fh)
        out = subprocess.run(["node", str(ROOT / "tests" / "run_code_node.js"), str(WF / workflow), name, fh.name],
                             capture_output=True, text=True, check=True)
        Path(fh.name).unlink()
        return json.loads(out.stdout)

    # ---- inbound: HubSpot notification parser (replaced 3 LLM calls)
    def parse(self, text):
        fx = {"nodes": {"IF HubSpot Message": {"Slack Message": [{"attachments": [{"text": text}]}]}}}
        return self.run_node("inbound-speed-to-lead.n8n.json", "Parse HubSpot Notification", fx)[0]["json"]

    def test_parses_owned_lead(self):
        r = self.parse("*Record ID*: 104729\n*Contact owner*: José O'Neil\n*SDR Owner*: Mia Chen")
        self.assertEqual(r, {"record_id": "104729", "contact_owner": "Jose O Neil", "sdr_owner": "Mia Chen"})

    def test_parses_unowned_lead(self):
        r = self.parse("*Record ID*: 55\n*Email*: lead@example.com")
        self.assertEqual(r, {"record_id": "55", "contact_owner": "", "sdr_owner": ""})

    def test_parser_survives_unexpected_text(self):
        r = self.parse("totally different message")
        self.assertEqual(r, {"record_id": "", "contact_owner": "", "sdr_owner": ""})

    # ---- NDA: PandaDoc webhook signature
    def verify(self, body, signature, key="test-shared-key"):
        fx = {"input": [{"body": json.loads(body)}], "query": {"signature": signature},
              "env": {"PANDADOC_WEBHOOK_KEY": key} if key else {}, "rawBody": body}
        return self.run_node("nda-automation.n8n.json", "Verify PandaDoc Signature", fx)

    BODY = json.dumps([{"event": "document_state_changed", "data": {"id": "doc1", "status": "document.approved"}}])

    def sign(self, body, key="test-shared-key"):
        return hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()

    def test_valid_signature_passes_through(self):
        out = self.verify(self.BODY, self.sign(self.BODY))
        self.assertIsInstance(out, list)
        self.assertEqual(out[0]["json"]["body"][0]["data"]["status"], "document.approved")

    def test_forged_approval_is_rejected(self):
        self.assertIn("Invalid PandaDoc webhook signature", self.verify(self.BODY, "0" * 64)["error"])

    def test_tampered_body_is_rejected(self):
        tampered = self.BODY.replace("doc1", "doc2")
        self.assertIn("error", self.verify(tampered, self.sign(self.BODY)))

    def test_missing_key_fails_closed(self):
        self.assertIn("not set", self.verify(self.BODY, self.sign(self.BODY), key=None)["error"])


if __name__ == "__main__":
    unittest.main()
