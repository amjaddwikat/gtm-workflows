#!/usr/bin/env python3
"""Structural lint for exported n8n workflows.

Catches the mistakes that break a workflow silently after a rename or a
copy-paste between workflows:

  * duplicate node names
  * connections pointing at nodes that do not exist
  * expressions referencing nodes that do not exist, e.g. $('Old Name')
  * invisible characters (combining marks, zero-width) in Code nodes,
    which editors and copy-paste silently mangle

Orphan nodes (no incoming edge, not a trigger) are reported as warnings.

Usage:  python scripts/lint_workflows.py [paths...]   (default: workflows/)
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

REF = re.compile(r"""\$\(\s*['"](.+?)['"]\s*\)|\$node\[\s*\\?["'](.+?)\\?["']\s*\]""")
EMOJI_PARTS = {"︎", "️", "‍"}   # variation selectors + ZWJ inside emoji, e.g. the ⚠️ in a message
TRIGGER_HINTS =("trigger", "webhook", "stickynote", "emailreadimap")


def lint(file: Path):
    data = json.loads(file.read_text(encoding="utf-8"))
    if "nodes" not in data:          # Make blueprint, nothing to lint here
        return [], []
    errors, warnings = [], []
    names = [n["name"] for n in data["nodes"]]
    known = set(names)
    for dup in {n for n in names if names.count(n) > 1}:
        errors.append(f"duplicate node name: {dup!r}")

    targets = set()
    for src, outputs in data.get("connections", {}).items():
        if src not in known:
            errors.append(f"connection from unknown node: {src!r}")
        for branch in outputs.get("main", []):
            for edge in branch or []:
                targets.add(edge["node"])
                if edge["node"] not in known:
                    errors.append(f"connection {src!r} -> unknown node {edge['node']!r}")

    for node in data["nodes"]:
        code = node.get("parameters", {}).get("jsCode", "")
        hidden = sorted({f"U+{ord(c):04X}" for c in code
                         if unicodedata.category(c) in ("Mn", "Cf") and c not in EMOJI_PARTS})
        if hidden:
            errors.append(f"node {node['name']!r} has invisible characters in its code "
                          f"({', '.join(hidden)}); write them as \\u escapes")
        for m in REF.finditer(json.dumps(node.get("parameters", {}), ensure_ascii=False)):
            ref = m.group(1) or m.group(2)
            if ref not in known:
                errors.append(f"node {node['name']!r} references unknown node {ref!r}")
        is_trigger = any(h in node["type"].lower() for h in TRIGGER_HINTS)
        if not is_trigger and node["name"] not in targets:
            warnings.append(f"orphan node (never executed): {node['name']!r}")
    return errors, warnings


def main(argv):
    targets = [Path(a) for a in argv] or [Path("workflows")]
    files = [f for t in targets for f in (sorted(t.rglob("*.json")) if t.is_dir() else [t])]
    n_err = 0
    for f in files:
        errors, warnings = lint(f)
        for e in errors:
            print(f"ERROR   {f}: {e}")
        for w in warnings:
            print(f"warning {f}: {w}")
        n_err += len(errors)
    print(f"\n{len(files)} file(s) linted, {n_err} error(s).")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
