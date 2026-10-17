import logging
import secrets
import uuid as _uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.api.tenancy import workspace_guard
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var
from app.core.responses import CompatJSONResponse
from app.db.session import engine
from sqlalchemy import text
from app.modules.etims.router import router as etims_router
from app.modules.auth.router import router as auth_router
from app.modules.workspaces.router import router as workspaces_router
from app.modules.clients.router import router as clients_router
from app.modules.suppliers.router import router as suppliers_router
from app.modules.items.router import router as items_router, products_router
from app.modules.accounts.router import router as accounts_router
from app.modules.accounts.ledger_router import router as ledger_router
from app.modules.accounts.posting import PeriodLockedError
from app.modules.documents.router import router as documents_router
from app.modules.payments.router import router as payments_router
from app.modules.reports.router import router as reports_router, analytics_router
from app.modules.crons.router import router as crons_router
from app.modules.hr.router import router as hr_router
from app.modules.payroll.router import router as payroll_router, admin_router as statutory_admin_router
from app.modules.crm.router import router as crm_router
from app.modules.operations.router import router as operations_router, inventory_router
from app.modules.cashbook.router import router as cashbook_router
from app.modules.governance.router import router as governance_router
from app.modules.admin.router import router as admin_router
from app.modules.bills.router import router as bills_router
from app.modules.group_consolidation.router import router as group_consolidation_router
from app.modules.billing.router import router as billing_router
from app.modules.finance.router import router as finance_router, tax_router, reconciliation_router
from app.modules.approvals.router import router as approvals_router
from app.modules.b2b.router import router as b2b_router
from app.modules.portal.router import router as portal_router, catalog_router, contact_router

settings = get_settings()
configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
logger = logging.getLogger("mannabooks")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema changes are applied by `alembic upgrade head` as a release step, not on every boot.
    # The guard only adds missing enum values (idempotent) and warns if the database is behind.
    from app.db.schema_guard import ensure_runtime_schema

    await ensure_runtime_schema()
    # AUTO_INIT_DB=true keeps the old behaviour for throwaway local databases only.
    if settings.AUTO_INIT_DB:
        from scripts.init_db import init_models

        try:
            await init_models()
        except Exception:
            logger.exception("Could not auto-initialize DB tables")
    yield


app = FastAPI(
    title="MannaBooks Decoupled API & KRA eTIMS Engine",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=CompatJSONResponse,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(PeriodLockedError)
async def period_locked_handler(request: Request, exc: PeriodLockedError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak internals (SQL, stack traces) to clients; log them with a reference id instead."""
    error_id = _uuid.uuid4().hex[:12]
    logger.exception("Unhandled error %s on %s %s", error_id, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"An unexpected error occurred. Reference: {error_id}"},
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Tag every request with an id (honours an incoming X-Request-ID) for log correlation."""
    incoming = request.headers.get("x-request-id", "")
    rid = incoming if 8 <= len(incoming) <= 64 and incoming.replace("-", "").isalnum() else _uuid.uuid4().hex
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


docs_security = HTTPBasic()


def authenticate_docs(credentials: HTTPBasicCredentials = Depends(docs_security)):
    if not settings.docs_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    expected_password = settings.docs_credentials_dict.get(credentials.username)
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Internal-Key", "X-Client-IP"],
)

# ---------------------------------------------------------------------------
# Routers. Every workspace-scoped router goes through `workspace_guard`, which
# requires a signed-in, active member of each referenced workspace
# (see app/api/tenancy.py for the rules format).
# ---------------------------------------------------------------------------
member = [Depends(workspace_guard())]
finance_only = [Depends(workspace_guard(permission="view_finance"))]

# Public or self-authenticating routers
app.include_router(auth_router, prefix="/v1")
app.include_router(admin_router, prefix="/v1")  # super admin only (router dependency)
app.include_router(statutory_admin_router, prefix="/v1")  # super admin only (router dependency)
app.include_router(crons_router, prefix="/v1")  # CRON_SECRET / internal key
app.include_router(portal_router, prefix="/v1")  # tokenised customer portal
app.include_router(catalog_router, prefix="/v1")  # public store catalog
app.include_router(contact_router, prefix="/v1")  # public contact form

# Workspace-scoped routers
app.include_router(workspaces_router, prefix="/v1", dependencies=[Depends(workspace_guard(rules=[
    (r"^/workspaces/[^/]+/(ledger/purge|clean-slate)$", "*", "__owner__"),
    (r"^/workspaces/[^/]+$", "DELETE", "__owner__"),
    (r"^/workspaces/[^/]+/(members|invitations)", "WRITE", "manage_team"),
    (r"^/workspaces/[^/]+/(settings|payment-methods|currencies|terms|ledger/repair)", "WRITE", "manage_settings"),
    (r"^/workspaces/[^/]+/billing/", "*", "manage_settings"),
    (r"^/workspaces/[^/]+/export", "*", "export_reports"),
]))])
app.include_router(etims_router, prefix="/v1", dependencies=[Depends(workspace_guard(
    public_paths=[r"^/etims/worker/"],
    rules=[(r"^/etims/initialize", "*", "manage_settings")],
))])
app.include_router(clients_router, prefix="/v1", dependencies=member)
app.include_router(suppliers_router, prefix="/v1", dependencies=member)
app.include_router(items_router, prefix="/v1", dependencies=member)
app.include_router(products_router, prefix="/v1", dependencies=member)
accounting_guard = [Depends(workspace_guard(permission="view_finance", rules=[
    (r"^/accounts", "WRITE", "manage_accounting"),
]))]
app.include_router(ledger_router, prefix="/v1", dependencies=accounting_guard)
app.include_router(accounts_router, prefix="/v1", dependencies=accounting_guard)
app.include_router(documents_router, prefix="/v1", dependencies=[Depends(workspace_guard(
    public_paths=[r"^/documents/public/", r"^/documents/webhooks/"],
))])
app.include_router(payments_router, prefix="/v1", dependencies=[Depends(workspace_guard(
    public_paths=[r"^/payments/mpesa/"],
))])
app.include_router(reports_router, prefix="/v1", dependencies=finance_only)
app.include_router(analytics_router, prefix="/v1", dependencies=[Depends(workspace_guard(permission="view_analytics"))])
hr_guard = [Depends(workspace_guard(rules=[
    (r"^/payroll/calculate$", "*", None),
    (r"^/(employees|payroll)", "*", "manage_payroll"),
    (r"^/expense-claims/[^/]+/(review|disburse)$", "*", "approve_requests"),
]))]
app.include_router(hr_router, prefix="/v1", dependencies=hr_guard)
app.include_router(payroll_router, prefix="/v1", dependencies=hr_guard)
app.include_router(crm_router, prefix="/v1", dependencies=[Depends(workspace_guard(
    public_paths=[r"^/crm/proposals/public/"],
))])
app.include_router(cashbook_router, prefix="/v1", dependencies=[Depends(workspace_guard(rules=[
    (r"^/(operations/)?(expenses|incomes)", "WRITE", "manage_expenses"),
]))])
app.include_router(operations_router, prefix="/v1", dependencies=member)
app.include_router(inventory_router, prefix="/v1", dependencies=member)
app.include_router(governance_router, prefix="/v1", dependencies=member)
app.include_router(bills_router, prefix="/v1", dependencies=[Depends(workspace_guard(rules=[
    (r"^/bills/[^/]+/(approve|pay)$", "*", "approve_requests"),
]))])
# Groups are authorised by group ownership (see tenancy.py) plus membership of any referenced workspace.
app.include_router(group_consolidation_router, prefix="/v1", dependencies=member)
app.include_router(billing_router, prefix="/v1", dependencies=[Depends(workspace_guard(
    public_paths=[r"^/billing/mpesa-callback$", r"^/billing/update-status/"],
    rules=[(r"^/billing/initiate", "*", "manage_settings")],
))])
app.include_router(finance_router, prefix="/v1", dependencies=finance_only)
app.include_router(tax_router, prefix="/v1", dependencies=finance_only)
app.include_router(reconciliation_router, prefix="/v1", dependencies=finance_only)
app.include_router(approvals_router, prefix="/v1", dependencies=[Depends(workspace_guard(rules=[
    (r"^/approvals/requests/[^/]+/decision$", "*", "approve_requests"),
]))])
# The B2B inbox currently operates on the workspace's own documents, so document ids are tenant-checked.
# Revisit when real cross-tenant e-invoicing (sender -> recipient) is built.
app.include_router(b2b_router, prefix="/v1", dependencies=member)


@app.get("/")
async def root():
    return {
        "service": "MannaBooks Decoupled API & KRA eTIMS Engine",
        "status": "online",
        "health": "/health",
    }


@app.get("/health")
async def health_check():
    """Liveness: the process is up (used by Railway's healthcheck)."""
    return {"status": "ok", "service": "mannabooksapi"}


@app.get("/health/ready")
async def readiness_check():
    """Readiness: the database is reachable."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "down"})
    return {"status": "ok", "database": "up"}
