from app.main import app

paths = app.openapi()["paths"]
print("=== REGISTERED FASTAPI OPENAPI ENDPOINTS ===")
for path, methods in paths.items():
    print(f"{list(methods.keys())[0].upper()} {path}")
print("=== TOTAL ENDPOINTS:", len(paths), "===")
