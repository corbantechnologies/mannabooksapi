import os
import sys
import re
import json
sys.path.insert(0, os.path.abspath('.'))
from app.main import app

def normalize_path(path: str) -> str:
    p = path.split('?')[0].strip()
    p = re.sub(r'\$\{[^}]+\}', '{id}', p)
    p = re.sub(r'\{[^}]+\}', '{id}', p)
    p = p.rstrip('/')
    return p if p else '/'

# 1. OpenAPI endpoints in FastAPI
schema = app.openapi()
openapi_paths = schema.get("paths", {})
fastapi_endpoints = {}

for path, methods in openapi_paths.items():
    norm_p = normalize_path(path)
    for method, op in methods.items():
        if method.lower() in ["get", "post", "put", "patch", "delete"]:
            m = method.upper()
            fastapi_endpoints[(m, norm_p)] = {
                "method": m,
                "path": path,
                "norm_path": norm_p,
                "summary": op.get("summary", ""),
                "operationId": op.get("operationId", "")
            }

# 2. Parse frontend calls with accurate method detection
frontend_dir = "../mannabooks"
frontend_src = os.path.join(frontend_dir, "src")
calls = []

for root, dirs, files in os.walk(frontend_src):
    for file in files:
        if file.endswith((".ts", ".tsx")):
            fpath = os.path.join(root, file)
            relpath = os.path.relpath(fpath, frontend_dir)
            with open(fpath, encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # axiosClient.(get|post|put|patch|delete)
            for m in re.finditer(r'axiosClient\.(get|post|put|patch|delete)\s*(?:<[^>]+>)?\s*\(\s*[`\'"]([^`\'"]+)', content):
                method = m.group(1).upper()
                raw_path = m.group(2).strip()
                p = raw_path if raw_path.startswith('/') else '/' + raw_path
                target = "/v1" + p
                calls.append({
                    "file": relpath,
                    "client": "axiosClient",
                    "method": method,
                    "raw_path": raw_path,
                    "target_path": target,
                    "norm_path": normalize_path(target)
                })

            # apiClient.(get|post|put|patch|delete)
            for m in re.finditer(r'apiClient\.(get|post|put|patch|delete)\s*(?:<[^>]+>)?\s*\(\s*[`\'"]([^`\'"]+)', content):
                method = m.group(1).upper()
                raw_path = m.group(2).strip()
                p = raw_path if raw_path.startswith('/') else '/' + raw_path
                target = p if p.startswith("/v1") else "/v1" + p
                calls.append({
                    "file": relpath,
                    "client": "apiClient",
                    "method": method,
                    "raw_path": raw_path,
                    "target_path": target,
                    "norm_path": normalize_path(target)
                })

            # apiClient(`path` or 'path', { method: "POST" })
            for m in re.finditer(r'apiClient\s*(?:<[^>]+>)?\s*\(\s*[`\'"]([^`\'"]+)[`\'"](?:\s*,\s*(\{.*?\bid\b|\{.*?\bmethod\b\s*:\s*[`\'"](POST|PUT|PATCH|DELETE|GET)[`\'"]))?', content, re.DOTALL):
                raw_path = m.group(1).strip()
                opts_match = m.group(3)
                method = opts_match.upper() if opts_match else "GET"
                p = raw_path if raw_path.startswith('/') else '/' + raw_path
                target = p if p.startswith("/v1") else "/v1" + p
                calls.append({
                    "file": relpath,
                    "client": "apiClient",
                    "method": method,
                    "raw_path": raw_path,
                    "target_path": target,
                    "norm_path": normalize_path(target)
                })

# Group unique calls
unique_calls = {}
for c in calls:
    key = (c["method"], c["norm_path"])
    if key not in unique_calls:
        unique_calls[key] = {
            "method": c["method"],
            "raw_paths": set(),
            "target_path": c["target_path"],
            "norm_path": c["norm_path"],
            "files": set(),
            "clients": set()
        }
    unique_calls[key]["raw_paths"].add(c["raw_path"])
    unique_calls[key]["files"].add(c["file"])
    unique_calls[key]["clients"].add(c["client"])

matched = {}
gaps = {}

for key, cinfo in sorted(unique_calls.items()):
    if key in fastapi_endpoints:
        matched[key] = (cinfo, fastapi_endpoints[key])
    else:
        # Check if route exists under any method
        alt_methods = [fa["method"] for (fa_m, fa_p), fa in fastapi_endpoints.items() if fa_p == cinfo["norm_path"]]
        gaps[key] = (cinfo, alt_methods)

print(f"=== FULL AUDIT RESULTS ===")
print(f"Total Unique Frontend Endpoints: {len(unique_calls)}")
print(f"FastAPI Endpoints Registered: {len(fastapi_endpoints)}")
print(f"Fully Matched & Operational: {len(matched)} ({len(matched)/len(unique_calls)*100:.1f}%)")
print(f"Identified Gaps (Missing/Method): {len(gaps)}")

# Group gaps by domain
domain_gaps = {}
for (m, norm_p), (cinfo, alt_m) in gaps.items():
    parts = norm_p.split('/')
    domain = parts[2] if len(parts) > 2 else "root"
    domain_gaps.setdefault(domain, []).append((cinfo, alt_m))

print("\n=== BREAKDOWN OF GAPS BY DOMAIN ===")
for domain, items in sorted(domain_gaps.items()):
    print(f"\n--- Domain: /{domain} ({len(items)} items) ---")
    for cinfo, alt_m in items:
        raw_p = list(cinfo["raw_paths"])[0]
        f_sample = list(cinfo["files"])[0]
        clients_str = ",".join(cinfo["clients"])
        if alt_m:
            print(f"  [{cinfo['method']}] {raw_p} -> FastAPI has different method: {alt_m} ({f_sample})")
        else:
            print(f"  [{cinfo['method']}] {raw_p} -> Missing in FastAPI ({f_sample}) [{clients_str}]")
