"""Extract every frontend -> API call: method, normalized path, body keys, and fields read from the response."""
import json
import os
import re
import sys

ROOT = sys.argv[1]
OUT = sys.argv[2]

CALL = re.compile(r"\b(axiosClient|apiClient)(?:\.(get|post|put|patch|delete))?\s*(?:<[^>()]*(?:<[^>]*>[^>()]*)*>)?\s*\(")
FETCH = re.compile(r"\bfetch\s*\(\s*([`\"'])(/api/v1/[^`\"']*)\1")
HREF = re.compile(r"([`\"'])(/api/v1/[^`\"'\s]*)\1")


def read_arg(s: str, i: int) -> tuple[str, int]:
    """Read one call argument starting at s[i]; returns (text, index after it). Handles nested template literals."""
    stack = []  # each entry: "code" (inside ${...} or brackets) or a quote char
    j = i
    while j < len(s):
        c = s[j]
        top = stack[-1] if stack else None
        if top in ("'", '"'):
            if c == "\\":
                j += 2
                continue
            if c == top:
                stack.pop()
        elif top == "`":
            if c == "\\":
                j += 2
                continue
            if c == "`":
                stack.pop()
            elif c == "$" and s[j + 1:j + 2] == "{":
                stack.append("${")
                j += 2
                continue
        else:
            if c in "\"'`":
                stack.append(c)
            elif c in "([{":
                stack.append(c)
            elif c in ")]}":
                if not stack:
                    return s[i:j].strip(), j
                stack.pop()
            elif c == "," and not stack:
                return s[i:j].strip(), j
        j += 1
    return s[i:j].strip(), j


def call_args(s: str, open_paren: int) -> list[str]:
    args, i = [], open_paren + 1
    while i < len(s):
        while i < len(s) and s[i] in " \t\r\n":
            i += 1
        if i < len(s) and s[i] == ")":
            break
        a, i = read_arg(s, i)
        args.append(a)
        if i < len(s) and s[i] == ",":
            i += 1
        else:
            break
    return args


def object_keys(text: str) -> tuple[list[str], bool]:
    """Top-level keys of an object literal; second value True if it contains a spread or isn't a literal."""
    t = text.strip()
    if t.startswith("JSON.stringify("):
        t = t[len("JSON.stringify("):-1].strip()
    if not t.startswith("{"):
        return [], True
    inner = t[1:-1]
    keys, spread, i = [], False, 0
    while i < len(inner):
        part, i = read_arg(inner, i)
        i += 1
        part = part.strip()
        if not part:
            continue
        if part.startswith("..."):
            spread = True
            continue
        m = re.match(r"""^["']?([A-Za-z_$][\w$]*)["']?\s*(:|$)""", part)
        if m:
            keys.append(m.group(1))
    return keys, spread


def norm_path(expr: str) -> tuple[str, list[str]] | None:
    e = expr.strip()
    if not e or e[0] not in "`\"'":
        return None
    body = e[1:-1]
    body = re.sub(r"\$\{[^}]*\}", "{p}", body)
    path, _, query = body.partition("?")
    qkeys = re.findall(r"([A-Za-z_]+)=", query)
    return path, qkeys


def response_reads(src: str, start: int, var: str, is_server: bool) -> list[str]:
    window = src[start:start + 4000]
    if is_server:
        pat = rf"\b{re.escape(var)}\??\.data\??\.([A-Za-z_]\w*)"
    else:
        pat = rf"\b{re.escape(var)}\??\.([A-Za-z_]\w*)"
    return sorted(set(re.findall(pat, window)) - {"then", "catch", "data", "error", "status", "length", "map", "filter"} if not is_server
                  else set(re.findall(pat, window)) - {"length", "map", "filter"})


calls = []
for dirpath, _, files in os.walk(ROOT):
    if "node_modules" in dirpath:
        continue
    for fn in files:
        if not fn.endswith((".ts", ".tsx")):
            continue
        fp = os.path.join(dirpath, fn)
        src = open(fp, encoding="utf-8").read()
        rel = os.path.relpath(fp, ROOT).replace("\\", "/")
        if rel in ("lib/api/client.ts", "lib/api/axios.ts"):
            continue
        for m in CALL.finditer(src):
            client, method = m.group(1), (m.group(2) or "").upper()
            args = call_args(src, m.end() - 1)
            if not args:
                continue
            np = norm_path(args[0])
            if not np:
                continue
            path, qkeys = np
            if client == "axiosClient":
                if path.startswith("/api/"):
                    continue  # Next.js route handlers, not the API
                path = "/v1" + path
            if not path.startswith("/v1"):
                continue
            body_keys, spread = [], False
            if client == "apiClient" and not method:
                opts = args[1] if len(args) > 1 else ""
                mm = re.search(r"method:\s*[\"'](\w+)", opts)
                method = mm.group(1).upper() if mm else "GET"
                bm = re.search(r"body:\s*(JSON\.stringify\(.*)", opts, re.S)
                if bm:
                    arg, _ = read_arg(bm.group(1), 0)
                    body_keys, spread = object_keys(arg)
                pm = re.search(r"params:\s*(\{.*)", opts, re.S)
                if pm:
                    arg, _ = read_arg(pm.group(1), 0)
                    qkeys += object_keys(arg)[0]
            elif method in ("POST", "PUT", "PATCH") and len(args) > 1:
                body_keys, spread = object_keys(args[1])
                if len(args) > 2:
                    pm = re.search(r"params:\s*(\{.*)", args[2], re.S)
                    if pm:
                        arg, _ = read_arg(pm.group(1), 0)
                        qkeys += object_keys(arg)[0]
            elif method in ("GET", "DELETE") and len(args) > 1:
                pm = re.search(r"params:\s*(\{.*)", args[1], re.S)
                if pm:
                    arg, _ = read_arg(pm.group(1), 0)
                    qkeys += object_keys(arg)[0]
            # Variable the result is assigned to
            pre = src[max(0, m.start() - 120):m.start()]
            vm = re.search(r"(?:const|let|var)\s+(\w+)(?:\s*:\s*[\w<>\[\]{}|, ]+)?\s*=\s*await\s*$", pre)
            reads = response_reads(src, m.end(), vm.group(1), client == "apiClient") if vm else []
            line = src.count("\n", 0, m.start()) + 1
            calls.append({"file": rel, "line": line, "client": client, "method": method or "GET", "path": path,
                          "query": sorted(set(qkeys)), "body": body_keys, "bodySpread": spread, "reads": reads,
                          "var": vm.group(1) if vm else None})
        for m in list(FETCH.finditer(src)) + list(HREF.finditer(src)):
            path = re.sub(r"\$\{[^}]*\}", "{p}", m.group(2)).split("?")[0].replace("/api/v1", "/v1", 1)
            line = src.count("\n", 0, m.start()) + 1
            calls.append({"file": rel, "line": line, "client": "link", "method": "GET", "path": path, "query": [],
                          "body": [], "bodySpread": False, "reads": [], "var": None})

json.dump(calls, open(OUT, "w"), indent=1)
print(len(calls), "frontend calls")
