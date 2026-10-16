"""Cross-check frontend calls against the backend inventory and print ranked findings."""
import json
import re
import sys
from collections import defaultdict

backend = json.load(open(sys.argv[1]))
frontend = json.load(open(sys.argv[2]))

GENERIC_READS = {"success", "message", "error", "detail", "data", "ok", "status"}
IGNORE_BODY = {"shop_id", "shopId"}  # read by the tenant guard even when the handler ignores it


def rx(path: str):
    return re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "/?$")


routes = [(rx(b["path"]), b) for b in backend]
findings = defaultdict(list)

for c in frontend:
    fe_path = c["path"].replace("{p}", "x123")
    cands = [b for r, b in routes if r.match(fe_path)]
    where = f'{c["file"]}:{c["line"]}'
    if not cands:
        findings["1_missing_route"].append(f'{c["method"]} {c["path"]}  ({where})')
        continue
    same = [b for b in cands if b["method"] == c["method"]]
    if not same:
        findings["2_wrong_method"].append(
            f'{c["method"]} {c["path"]} -> backend has {sorted({b["method"] for b in cands})}  ({where})')
        continue
    # Prefer a literal match over a {param} match (e.g. /fiscal-years vs /{account_id}).
    same.sort(key=lambda b: b["path"].count("{"))
    b = same[0]

    if c["body"] and b["body"] is not None and not (b["bodyKind"] == "dict" and not b["body"]):
        accepted = set(b["body"])
        if "<list>" not in accepted:
            ignored = [k for k in c["body"] if k not in accepted and k not in IGNORE_BODY]
            if ignored:
                findings["3_ignored_body_fields"].append(
                    f'{c["method"]} {b["path"]}: sends {ignored} not read by {b["func"]}  ({where})')

    sent_q = set(c["query"])
    missing_q = [q for q in b.get("requiredQuery", []) if q not in sent_q]
    if missing_q and c["client"] != "link":
        findings["4_missing_required_query"].append(f'{c["method"]} {b["path"]}: missing {missing_q}  ({where})')
    extra_q = [q for q in sent_q if q not in set(b["query"]) and q not in ("shop_id", "shop_slug")]
    if extra_q:
        findings["6_ignored_query"].append(f'{c["method"]} {b["path"]}: query {extra_q} not used  ({where})')

    resp = b["response"]
    if c["reads"] and resp is not None and "*" not in resp:
        keys = set(resp)
        missing = [k for k in c["reads"] if k not in keys and k not in GENERIC_READS]
        if missing:
            findings["5_response_fields_not_returned"].append(
                f'{c["method"]} {b["path"]}: reads {missing}; returns {sorted(k for k in keys if not k.startswith("<"))[:14]}  ({where})')

for k in sorted(findings):
    print(f"\n## {k} ({len(findings[k])})")
    for line in sorted(set(findings[k])):
        print(" -", line)
