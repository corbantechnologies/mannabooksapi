import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import get_settings

settings = get_settings()


@pytest.mark.asyncio
async def test_docs_basic_auth_protection():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Unauthenticated request to /docs should receive 401
        res_unauth = await ac.get("/docs")
        assert res_unauth.status_code == 401
        assert "WWW-Authenticate" in res_unauth.headers

        # 2. Authenticated request with valid basic credentials should succeed (200)
        # Using default credentials from config
        res_auth = await ac.get("/docs", auth=("admin", "test-docs-password"))
        assert res_auth.status_code == 200
        assert "swagger-ui" in res_auth.text.lower() or "html" in res_auth.text.lower()

        # 3. Request with wrong password should fail (401)
        res_wrong = await ac.get("/docs", auth=("admin", "wrong_password"))
        assert res_wrong.status_code == 401


@pytest.mark.asyncio
async def test_auth_registration_and_login_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        unique_suffix = uuid.uuid4().hex[:6]
        test_email = f"merchant_{unique_suffix}@mannabooks.co.ke"

        headers = {
            "Content-Type": "application/json",
        }

        # 1. Register new merchant
        reg_payload = {
            "name": f"Test Merchant {unique_suffix}",
            "email": test_email,
            "password": "Password123!",
            "business_name": f"Super Mart {unique_suffix}",
        }
        res_reg = await ac.post("/v1/auth/register", json=reg_payload, headers=headers)
        assert res_reg.status_code == 200
        data_reg = res_reg.json()
        assert data_reg["success"] is True
        assert data_reg["token"] is not None
        assert data_reg["shop_slug"] is not None
        assert data_reg["user"]["email"] == test_email

        # 2. Duplicate registration attempt should return error
        res_dup = await ac.post("/v1/auth/register", json=reg_payload, headers=headers)
        assert res_dup.status_code == 200
        data_dup = res_dup.json()
        assert data_dup["success"] is False
        assert "already exists" in data_dup["error"].lower()

        # 3. Login with the registered credentials
        login_payload = {
            "email": test_email,
            "password": "Password123!",
        }
        res_login = await ac.post("/v1/auth/login", json=login_payload, headers=headers)
        assert res_login.status_code == 200
        data_login = res_login.json()
        assert data_login["success"] is True
        assert data_login["token"] is not None

        # 4. Verify session with /v1/auth/me
        auth_headers = {
            "Authorization": f"Bearer {data_login['token']}",
        }
        res_me = await ac.get("/v1/auth/me", headers=auth_headers)
        assert res_me.status_code == 200
        data_me = res_me.json()
        assert data_me["success"] is True
        assert data_me["user"]["email"] == test_email
