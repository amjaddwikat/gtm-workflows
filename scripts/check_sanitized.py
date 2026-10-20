#!/usr/bin/env python3
"""Fail if an exported n8n / Make workflow still carries client or prospect data.

Exported workflows routinely leak more than API keys: pinned test executions
hold real lead records, and webhook URLs for Clay / Make *are* the credential.
This check runs in CI on every push and PR so that a raw export can never be
merged by accident.

Usage:  python scripts/check_sanitized.py [paths...]   (default: workflows/)
Exit code 1 lists every finding as  file:path  rule  value.
"""
import json
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"YOUR_|REDACTED|example\.(com|org)|your-webhook-path", re.I)

# (rule name, regex) checked against every string value in the JSON tree
STRING_RULES = [
    ("email address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("clay webhook url", re.compile(r"api\.clay\.com/v3/sources/webhook/[A-Za-z0-9_-]+")),
    ("make webhook url", re.compile(r"hook\.[a-z0-9]+\.make\.com/[A-Za-z0-9_]+")),
    ("n8n webhook url", re.compile(r"https?://(?!api\.clay\.com)[^\s\"']+/webhook(-test)?/[A-Za-z0-9_-]+")),
    ("google sheet id", re.compile(r"spreadsheets/d/[A-Za-z0-9_-]{25,}|^1[A-Za-z0-9_-]{40,}$")),
    ("hubspot portal id", re.compile(r"app\.hubspot\.com/[a-z-]+/\d{5,}")),
    ("linkedin profile", re.compile(r"linkedin\.com/in/[A-Za-z0-9_%-]+")),
    ("slack id", re.compile(r"(?<![A-Za-z0-9])[CDGU]0[A-Z0-9]{7,10}(?![A-Za-z0-9])")),
    ("api key in query", re.compile(r"(api_key|apikey|token|access_token)=[^&\"\s{]+", re.I)),
    ("bearer / key header", re.compile(r"(Bearer|API-Key)\s+[A-Za-z0-9._-]{16,}")),
    ("openai key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("github token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack token", re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}")),
]

# keys whose mere presence (with a non-placeholder value) is a leak
KEY_RULES = {
    "instanceId": "n8n instance id",
    "webhookId": "n8n webhook id",
}


def walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}" if path else k)
            if k in KEY_RULES and v and not (isinstance(v, str) and PLACEHOLDER.search(v)):
                yield path, KEY_RULES[k], str(v)[:60]
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")
    elif isinstance(node, str):
        for rule, rx in STRING_RULES:
            for m in rx.finditer(node):
                if not PLACEHOLDER.search(m.group(0)):
                    yield path, rule, m.group(0)[:80]


def check(file: Path):
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [(str(file), "invalid json", str(e))]
    findings = []
    if isinstance(data, dict) and data.get("pinData"):
        findings.append((f"{file}:pinData", "pinned execution data", "must be {}"))
    findings += [(f"{file}:{p}", rule, val) for p, rule, val in walk(data)]
    return findings


def main(argv):
    targets = [Path(a) for a in argv] or [Path("workflows")]
    files = [f for t in targets for f in (sorted(t.rglob("*.json")) if t.is_dir() else [t])]
    findings = [f for file in files for f in check(file)]
    for where, rule, val in findings:
        print(f"{where}  [{rule}]  {val}")
    print(f"\n{len(files)} file(s) checked, {len(findings)} finding(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
