# Strauss Car Advisor

A Hebrew assistant for the Strauss company-car challenge (Open Source AI Hackathon 2026). An
employee chats with the advisor, fills in a few personal details, and gets a table that compares
today's car arrangement with alternatives (another car, an electric car, or giving up the car) by
monthly net pay. When the advisor lacks a figure that only the transport representative knows, it
sends the representative a question. The representative answers on a separate screen, and the
conversation resumes by itself.

- **Employee screen:** chat, a panel with the employee's details, and the comparison table.
- **Rep screen:** the open and answered questions, and a form to answer each one.
- **Backend:** FastAPI, Pydantic AI (OpenAI), SQLAlchemy and SQL Server.
- **Frontend:** Angular 22 with right-to-left Hebrew.
- **Data:** the 2026 usage-value dataset (77,988 records), and the challenge's car catalog,
  occupations table and payroll rules.

The screens have no login and the salary data is fictitious. The car catalog and the occupations
are the challenge's data of 2026-10-12: the codes and usage values are real, the costs and
participations are not. The engine reproduces the challenge's six sample employees to the shekel.
The limitations are listed in the submission summary of
[docs/explanations.he.md](docs/explanations.he.md), which explains the whole system in Hebrew.

## Prerequisites

To run the app (quick start):

- Docker with Compose v2, and enough memory for SQL Server (2 GB or more).

To work on the code (dev mode and `./check.sh`), also:

- [uv](https://docs.astral.sh/uv/) 0.12.23 and Python 3.12 (uv installs Python itself if needed)
- Node.js 22.22 or later, with npm

## Quick start

```bash
cp .env.example .env
```

Edit `.env`:

- Set `DB_PASSWORD` to a password that SQL Server accepts: at least 8 characters, from at least
  three of: upper case, lower case, digits and symbols.
- Set `OPENAI_API_KEY` to your key. Without it the app still starts, but the advisor does not
  answer: the chat shows a notice, and the details panel and the car picker keep working.

Then start everything:

```bash
docker compose up
```

The first start builds the images and loads the dataset into the database, which takes a few
minutes. Later starts skip the load, because the data stays in a Docker volume. When the log
shows no more errors, open:

- Employee screen: <http://127.0.0.1:8080/employee>
- Rep screen: <http://127.0.0.1:8080/rep>
- Health check: <http://127.0.0.1:8000/api/health> (200 with the number of records)

All the ports are bound to `127.0.0.1`, so the app is reachable from this machine only.

Stop with `Ctrl+C`. `docker compose down` removes the containers and keeps the data, and
`docker compose down -v` also deletes the database.

Notes:

- `config/` and `data/` are mounted read-only into the backend container, which runs as an
  unprivileged user. Their files must be readable by everyone (`chmod -R a+rX config data`), which
  is the default unless a strict `umask` was used when the repository was cloned.
- After editing `config/*.yaml` or `.env`, run `docker compose restart backend`.

## Dev mode

Run the database in Docker, and the backend and the frontend on your machine:

```bash
docker compose up -d db

# Backend, in one terminal. DB_HOST in .env must be 127.0.0.1 (not localhost).
# uvicorn listens on 127.0.0.1:8000 by default.
cd backend
uv sync
uv run uvicorn car_advisor.api.app:app --reload

# Frontend, in another terminal. It proxies /api to the backend.
cd frontend
npm ci
npx ng serve
```

The frontend is then at <http://localhost:4200/employee>.

## Dataset

The dataset is `data/usage-values-2026.csv` (windows-1255). The backend loads it on its first
start, when the database holds no records of `TAX_YEAR`. To reload a year by hand, which replaces
the records of that year:

```bash
cd backend
uv run car-advisor-import --tax-year 2026
```

## Checks

`./check.sh` runs everything: ruff and pytest for the backend, then lint, formatting and the unit
tests for the frontend. It needs no network and no API key.

The tests that need a live SQL Server are skipped unless `TEST_MSSQL_URL` is set:

```bash
docker compose up -d db
cd backend
TEST_MSSQL_URL='mssql+mssqlpython://sa:<DB_PASSWORD>@127.0.0.1:1433/master' uv run pytest -m mssql
```

## Where things are

| What | Where |
|---|---|
| App settings (database, model, API key, paths) | `.env`, copied from `.env.example` |
| Calculation rules, tax parameters (the challenge's 2025 tables), budgets and the items pending from Strauss | `config/rules.yaml` |
| Car catalog (the challenge's data; replace with Strauss's) | `config/car_catalog.yaml` |
| Occupations table (the challenge's data; replace with Strauss's) | `config/occupations.yaml` |
| Usage-value dataset | `data/usage-values-2026.csv` |
| Backend code | `backend/src/car_advisor/` |
| Frontend code | `frontend/src/app/` |
| Explanations in Hebrew, with the submission summary and the open questions | `docs/explanations.he.md` |
