from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import get_settings
from scripts.init_db import init_models
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
from app.modules.crons.router import router as crons_router
from app.modules.hr.router import router as hr_router
from app.modules.crm.router import router as crm_router
from app.modules.operations.router import router as operations_router
from app.modules.governance.router import router as governance_router
from app.modules.admin.router import router as admin_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Automatically verify and create tables if not exists on startup
    try:
        await init_models()
    except Exception as e:
        print(f"[Startup Warning] Could not auto-initialize DB tables: {e}")
    yield


import secrets
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi

app = FastAPI(
    title="MannaBooks Decoupled API & KRA eTIMS Engine",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

docs_security = HTTPBasic()


def authenticate_docs(credentials: HTTPBasicCredentials = Depends(docs_security)):
    creds = settings.docs_credentials_dict
    if not creds:
        # Default fallback if empty
        creds = {"admin": "mannabooks2026"}
    expected_password = creds.get(credentials.username)
    if not expected_password or not secrets.compare_digest(credentials.password, expected_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True


@app.get("/docs", include_in_schema=False)
async def get_swagger_docs(auth: bool = Depends(authenticate_docs)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - API Documentation")


@app.get("/openapi.json", include_in_schema=False)
async def get_openapi_schema(auth: bool = Depends(authenticate_docs)):
    return get_openapi(title=app.title, version=app.version, routes=app.routes)

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
app.include_router(crons_router, prefix="/v1")
app.include_router(hr_router, prefix="/v1")
app.include_router(crm_router, prefix="/v1")
app.include_router(operations_router, prefix="/v1")
app.include_router(governance_router, prefix="/v1")
app.include_router(admin_router, prefix="/v1")


@app.get("/")
async def root():
    return {
        "service": "MannaBooks Decoupled API & KRA eTIMS Engine",
        "status": "online",
        "env": settings.ENVIRONMENT,
        "docs": "/docs" if not settings.is_production else None,
        "health": "/health",
    }


@app.get("/health")
async def health_check():
    return {"status": "ok", "env": settings.ENVIRONMENT, "service": "mannabooksapi"}
