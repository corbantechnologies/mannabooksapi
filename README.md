# MannaBooks API — Decoupled FastAPI Backend

FastAPI backend engine and native **KRA eTIMS (OSCU)** fiscalization microservice for MannaBooks.

## Architecture & Integration
- **Framework:** FastAPI (Python 3.12+ async)
- **Database:** PostgreSQL on Railway via SQLAlchemy 2.0 Async + asyncpg
- **eTIMS Integration Type:** KRA OSCU (Online Sales Control Unit)
- **Cryptographic Security:** AES-256-CBC with PKCS7 padding for taxpayer communication keys
- **Pattern:** Transactional Outbox pattern with optimistic synchronous 2.5s execution & asynchronous resilient retry queue

## Local Development
1. Create virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy environment configuration:
   ```bash
   cp .env.example .env
   ```
4. Start development server:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```
5. Swagger API Docs: [http://localhost:8000/docs](http://localhost:8000/docs)

## Production Deployment (Railway)
- Root directory: `mannabooksapi/`
- Build command: `pip install -r requirements.txt` or Dockerfile
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
