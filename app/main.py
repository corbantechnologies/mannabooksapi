from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import get_settings
from app.modules.etims.router import router as etims_router
from app.modules.auth.router import router as auth_router
from app.modules.workspaces.router import router as workspaces_router
from app.modules.clients.router import router as clients_router
from app.modules.suppliers.router import router as suppliers_router
from app.modules.items.router import router as items_router
from app.modules.accounts.router import router as accounts_router
from app.modules.documents.router import router as documents_router
from app.modules.payments.router import router as payments_router
from app.modules.reports.router import router as reports_router

settings = get_settings()

app = FastAPI(
    title="MannaBooks Decoupled API & KRA eTIMS Engine",
    version="1.0.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
)

# CORS restricted in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.is_production else ["https://www.mannabooks.co.ke"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Domain Routers under /v1
app.include_router(auth_router, prefix="/v1")
app.include_router(workspaces_router, prefix="/v1")
app.include_router(etims_router, prefix="/v1")
app.include_router(clients_router, prefix="/v1")
app.include_router(suppliers_router, prefix="/v1")
app.include_router(items_router, prefix="/v1")
app.include_router(accounts_router, prefix="/v1")
app.include_router(documents_router, prefix="/v1")
app.include_router(payments_router, prefix="/v1")
app.include_router(reports_router, prefix="/v1")


@app.get("/health")
async def health_check():
    return {"status": "ok", "env": settings.ENVIRONMENT, "service": "mannabooksapi"}
