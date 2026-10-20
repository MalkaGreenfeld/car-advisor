# Implementation Plan: Strauss Car Advisor

## Overview

The plan builds the app bottom-up, so every layer is tested before the next one uses it: repository and tooling, database and dataset, rules config and mock data, the pure engine, conversations, the agent, the API, the Angular frontend, packaging, documentation, and a final live verification. Module names, signatures, versions and test locations come from `design.md`.

Path shorthand: `car_advisor/` is `backend/src/car_advisor/`, `tests/` is `backend/tests/`, and `app/` is `frontend/src/app/`. Other paths are relative to the repository root.

Rules for every task:
- Write idiomatic, fully typed code: ruff for Python and strict TypeScript for Angular, with PEP 8 and Angular style-guide naming.
- Add comments only for a non-obvious "why". Write no narration comments, banners, emoji, TODOs or dead code.
- Code, identifiers, comments, logs and commit messages are in English. Hebrew appears only in UI copy, bot replies, user-facing error messages and `docs/explanations.he.md`. Backend Hebrew lives in `car_advisor/engine/copy_he.py`, and frontend Hebrew lives in the `*.copy.ts` files. Groups are `A`-`D` in code and א-ד in the UI.
- Keep data, rules config, pure engine, agent tools, API and UI in separate modules, and follow the design's module dependency table. Build nothing outside the design's scope.
- Tests never call live OpenAI and never need network access. Each property test uses the shared Hypothesis profile, starts with `# Feature: strauss-car-advisor, Property <n>: <title>`, and puts the strategies it needs in `tests/strategies.py`.
- Create no git commits unless the user asks.

Optional tasks are marked `*`. Only two kinds are optional: Requirement 13 (document upload) and property-based tests. Unit tests, including the challenge rule examples, are required.

## Tasks

- [x] 1. Set up the repository and backend tooling
  - [x] 1.1 Initialize git and move the dataset into `data/`
    - Run `git init`. Write `.gitignore` with `.env`, `node_modules/`, `.venv/`, `dist/`, `.angular/`, `__pycache__/`, `.kiro/handoff/` and the challenge PDF by its file name
    - Move the CSV to `data/usage-values-2026.csv`. Compare `sha256sum` before and after the move, and tell the user the new path
    - Verify: the hashes match, and `git status --short` lists `.gitignore` and `data/` but neither the PDF nor `.kiro/handoff/`
    - _Requirements: 14.13, 14.14_

  - [x] 1.2 Create the backend uv project
    - `backend/pyproject.toml`: Python 3.12, a src layout with the package `car_advisor`, and the exact pins from the design's tech stack table
      - runtime: fastapi 0.143.0, uvicorn 0.54.0, pydantic 2.14.1, pydantic-settings 2.15.0, pydantic-ai-slim[openai] 2.55.0, sqlalchemy 2.1.4, alembic 1.20.0, mssql-python 1.16.0, pyyaml 6.0.3, python-multipart 0.0.32
      - dev group: pytest 9.1.1, hypothesis 6.169.0, ruff 0.17.0, httpx 0.28.1
    - Add `[project.scripts] car-advisor-import = "car_advisor.dataset.cli:main"`, the ruff lint and format settings, and a pytest config that registers the `mssql` marker
    - Create `car_advisor/__init__.py` and generate `backend/uv.lock` with `uv lock`
    - `tests/conftest.py`: register and load the Hypothesis profile `default` (`max_examples=100, deadline=None, derandomize=True, database=None`)
    - Verify: `uv sync`, `uv run ruff check` and `uv run ruff format --check` pass in `backend/`
    - _Requirements: 15.3, 15.5, 15.7_

  - [x] 1.3 Implement App_Settings and `.env.example`
    - `car_advisor/settings.py`: `Settings` exactly as the design defines it: the `NonEmpty` type, the `db_name` identifier pattern, `SecretStr` for the password and the API key, `env_file="../.env"`, `env_ignore_empty=True`, and the default config paths
    - `.env.example` at the root lists every variable: `DATASET_PATH`, `TAX_YEAR`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `LLM_PROVIDER`, `LLM_MODEL`, `OPENAI_API_KEY` and the three config paths. Use a placeholder password that meets SQL Server's complexity rules, and leave `OPENAI_API_KEY` empty
    - `tests/test_settings.py` checks that:
      - `.env.example` parses into `Settings` with `openai_api_key is None`;
      - a missing or empty required variable and an invalid `DB_NAME` each fail, naming the variable;
      - the validation error text never contains the input value
    - _Requirements: 3.21, 14.3, 14.4, 14.5, 17.12_

  - [x] 1.4 Implement structured request logging
    - `car_advisor/logs.py`:
      - a JSON-lines formatter that keeps only the design's whitelisted keys (`ts`, `level`, `event`, `method`, `route`, `status`, `duration_ms`, `tool`, `ok`, `conversation_id`, `stage`, `error_type`, `tax_year`, `record_count`, `fields`);
      - `configure_logging()`, which writes to stdout;
      - `RequestLogMiddleware`, which logs the route template from `scope["route"].path` and the `conversation_id` path parameter
    - `tests/test_logs.py` uses `caplog` and a minimal FastAPI app to check that:
      - each request writes one line with method, route template, status and `duration_ms`;
      - keys outside the whitelist are dropped;
      - the raw URL and the query string never appear
    - _Requirements: 14.10, 14.11_

  - [x] 1.5 Add the `check.sh` skeleton
    - Root `check.sh`, executable, with `set -euo pipefail`. In `backend/` it runs `uv run ruff check`, `uv run ruff format --check` and `uv run pytest`. Task 14.4 adds the frontend steps
    - Verify: `./check.sh` exits 0, and it exits non-zero when one file is deliberately misformatted. Revert that file afterwards
    - _Requirements: 15.5_

- [x] 2. Build the database layer and the dataset
  - [x] 2.1 Implement the shared engine types
    - `car_advisor/engine/__init__.py` and `car_advisor/engine/types.py`, as in the design: `Group`, `Powertrain`, `Money`, `Percent`, `CarKey` (`__str__` gives `2026-2020-1-01229-00001`, plus `parse`), `CarRecord`, `PayComponent`, `CurrentCarDescription`, `EmployeeProfile`, `CarAlternative`, `WaiverAlternative`, `Alternative`, `MissingField` and `RepParticipation`
    - `tests/engine/test_types.py`:
      - a `CarKey` string round trip keeps leading zeros, and `parse` rejects a malformed key;
      - the codes accept digit strings only;
      - `EmployeeProfile` rejects group E, a negative amount, 101 %, negative credit points and a fractional children count
    - _Requirements: 1.3, 1.4, 11.3, 15.2_

  - [x] 2.2 Define the tables and the SQLite test database
    - `car_advisor/db.py`: the declarative `Base`
    - `car_advisor/dataset/models.py`: `UsageValueRow`
      - the Official_Key composite primary key, with the codes as `VARCHAR(10)`;
      - the `(tax_year, registration_year)` index;
      - `NVARCHAR` text columns, plus `electric_raw`, `powertrain` and `effective_date`
    - `car_advisor/conversations/models.py`: `ConversationRow`, `MessageRow` and `RepQuestionRow`, with the columns from the design's tables
      - `version` is the `version_id_col`;
      - `ux_rep_questions_open` is declared with both `mssql_where` and `sqlite_where`, next to `ix_rep_questions_status_created`
    - `tests/conftest.py`: a `sqlite+pysqlite://` engine with `StaticPool` and `Base.metadata.create_all`, plus a session factory fixture
    - `tests/test_models.py`:
      - the four tables exist;
      - a second open row for the same conversation, field and car is rejected, also when the car is NULL;
      - a new open row is accepted once the first is answered
    - _Requirements: 1.4, 10.4, 14.7_

  - [x] 2.3 Implement database setup and the first migration
    - `car_advisor/db.py`: `database_url`, `make_engine`, `ensure_database`, `run_migrations` and `make_session_factory`, as in the design
      - Confirm the mssql-python query keys for `Encrypt` and `TrustServerCertificate`.
      - Switch to the pyodbc + msodbcsql18 fallback only if mssql-python fails, and tell the user if you do.
    - `backend/alembic.ini` and `backend/migrations/env.py`, which uses the connection passed in `config.attributes`
    - `backend/migrations/versions/0001_initial_schema.py`, which creates the four tables and their indexes
    - Root `compose.yaml` with only the `db` service: image tag, environment, `127.0.0.1:1433`, the `mssql-data` volume and the healthcheck, exactly as in the design
    - `tests/test_db.py`: `database_url` uses `mssql+mssqlpython`, and the password never appears in `str(url)` or `repr(url)`
    - `tests/test_mssql_smoke.py` is marked `mssql` and skipped unless `TEST_MSSQL_URL` is set. It checks that:
      - `ensure_database` creates the database;
      - running the migrations twice is a no-op;
      - the filtered unique index holds;
      - Hebrew text round-trips through `NVARCHAR`
    - Verify: `uv run pytest` passes with the smoke tests skipped, and `docker compose config --quiet` succeeds with a `.env` copied from `.env.example`
    - _Requirements: 14.7, 14.8, 14.16_

  - [x] 2.4 Implement the dataset parser
    - `car_advisor/dataset/importer.py`: `DatasetImportError`, `derive_powertrain` (3 electric, 1 hybrid, 2 plug_in, 0 conventional) and `parse_dataset`
      - `parse_dataset` reads with `encoding="windows-1255", newline=""` and `csv.reader`.
      - It checks rows in the design's order and stops at the first failure, with that line number and the reason.
      - A repeated key names both lines.
    - `tests/dataset/test_parse.py` uses small windows-1255 files written to `tmp_path`:
      - fields are trimmed, and codes keep their leading zeros;
      - dates are read day first, and `31/02/2026` is rejected;
      - each row failure reports its line and its reason;
      - a missing file, an undecodable file and a header-only file each raise with the path and the reason
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.13, 1.18, 1.19_

  - [x] 2.5 Implement the tax-year import
    - `importer.py`: `ImportResult`, `has_records` and `import_tax_year`. The delete and the bulk insert run inside one `sessions.begin()` block
    - `tests/conftest.py`: a session-scoped fixture that imports the real `data/usage-values-2026.csv` into SQLite once
    - `tests/dataset/test_import.py`:
      - the real file imports 77,988 records for 2026;
      - a reload replaces that year;
      - a failed write keeps the previous records
    - `tests/dataset/test_powertrain_models.py` runs on the real file
      - Electric: Tesla Model 3, Tesla Model Y, Hyundai Ioniq 5, BYD Atto 3, Volvo EX30, Volkswagen ID.4, Toyota bZ4X and Kia Niro EV.
      - Not electric: the hybrid and plug-in rows of Toyota Corolla and Toyota Prius.
      - Find the keys with a query against the file, then hard-code them in the test.
    - _Requirements: 1.6, 1.11, 1.12, 1.17, 15.10_

  - [ ]* 2.6 Write property test for the CSV parse round trip
    - **Property 1: CSV parse round trip**
    - `tests/dataset/test_dataset_properties.py`
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.6, 1.7, 1.8, 1.9, 1.10**

  - [ ]* 2.7 Write property test for malformed-row reporting
    - **Property 2: The first malformed row is the one reported**
    - **Validates: Requirements 1.5, 1.13, 1.19**

  - [ ]* 2.8 Write property test for the single-year atomic import
    - **Property 3: An import replaces exactly one tax year, atomically**
    - **Validates: Requirements 1.11, 1.12**

  - [x] 2.9 Implement Car_Lookup
    - `car_advisor/dataset/lookup.py`, as in the design: `KeyParts`, `CarCandidate`, the result types `Found`, `NotFound`, `Rejected`, `LookupFailed` and `Candidates`, and `CarLookup` with `get`, `get_many`, `search` and `missing_keys`
      - `search` escapes `%`, `_`, `[` and `\`, matches `UPPER(...) LIKE` on the manufacturer and trade name, and orders by manufacturer, trade name and model code.
      - A `SQLAlchemyError` becomes `LookupFailed`.
    - `tests/dataset/test_lookup.py` runs on the real file:
      - a full key returns exactly one record, with its Usage_Value;
      - a trade name alone returns 2 or more candidates (use a Nissan Pathfinder registration year verified against the file);
      - a search with no match returns an empty list with `none_found`;
      - `%` and `_` in the text match literally;
      - an unreachable database gives `LookupFailed`;
      - a key lookup takes under 100 ms, and a search under 500 ms
    - Add the same two timings on SQL Server to `tests/test_mssql_smoke.py`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.11, 2.12, 2.13, 2.14_

  - [ ]* 2.10 Write property test for exact key lookup
    - **Property 4: Key lookup is exact**
    - `tests/dataset/test_lookup_properties.py`
    - **Validates: Requirements 2.1, 2.2, 2.3**

  - [ ]* 2.11 Write property test for incomplete lookup requests
    - **Property 5: Incomplete requests are rejected, naming every missing input**
    - **Validates: Requirements 2.4, 2.13**

  - [ ]* 2.12 Write property test for search against a reference filter
    - **Property 6: Search equals a reference filter**
    - **Validates: Requirements 2.5, 2.7, 2.8**

  - [x] 2.13 Add the manual import command
    - `car_advisor/dataset/cli.py`: `main()`
      - It parses `--tax-year` (required) and `--path` (the App_Settings path by default).
      - It runs `ensure_database`, `run_migrations` and `import_tax_year`, which always reloads the year.
      - It prints the tax year and the record count. On a `DatasetImportError` it prints the message and exits non-zero.
    - `tests/dataset/test_cli.py` patches the database setup to SQLite:
      - a small file reloads its year and prints the count;
      - a malformed file exits non-zero, with the line and the reason
    - Verify: `uv run car-advisor-import --help` works
    - _Requirements: 1.16, 1.17, 1.18_

- [x] 3. Load the rules config and the mock data
  - [x] 3.1 Implement the config schemas
    - `car_advisor/engine/config.py`: `RulesConfig` and its nested models, plus `CatalogCar`, `CarCatalog`, `Occupation` and `OccupationsTable`
      - Every model uses `extra="forbid"`.
      - `CarCatalog` has `find(key)` and `cars_for(group, order)`.
      - `OccupationsTable.find(name)` compares names after `strip()`.
    - Implement every row of the design's validation table:
      - money at least 0, and percentages within 0-100;
      - Family_Track shares that sum to 100;
      - a strict whole-number threshold of at least 1;
      - a group order that is a permutation of A-D, and all four budgets;
      - contiguous ascending brackets;
      - the `Literal` fields, and a non-empty `source` in every parameter group;
      - a `pending` list that is complete and in sync with its values;
      - unique catalog keys, and occupation names unique after trimming;
      - strict string codes, so an unquoted `00001` is rejected
    - `tests/engine/test_config.py`:
      - a minimal valid config passes;
      - each single violation fails, naming the field's dotted path;
      - `cars_for` returns the employee's group and the lower groups;
      - `find` trims names
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.17, 3.23, 4.2, 4.10, 4.15, 4.16_

  - [x] 3.2 Implement the config loader
    - `car_advisor/rules/__init__.py` and `car_advisor/rules/loader.py`: `AppConfig`, `ConfigError(path, problems)` and `load_config(rules_path, catalog_path, occupations_path)`. Each file goes through three checks:
      - existence: a missing file gives `file not found: <path>`;
      - parsing: `yaml.safe_load`, and a `MarkedYAMLError` gives `line <n>: <problem>`;
      - validation: each Pydantic error gives `<dotted.loc>: <msg>`. Catalog and occupation errors name the entry by position and name, for example `cars[3] (Kia Niro EV).monthly_cost: ...`
    - `tests/rules/test_loader.py`:
      - a missing file is reported;
      - a YAML error is reported at its line;
      - a rules file with three invalid fields lists all three;
      - a catalog with two invalid cars names both
    - _Requirements: 3.16, 3.17, 3.22, 4.15, 4.16_

  - [x] 3.3 Write the tracked `config/rules.yaml`
    - Copy the design's `rules.yaml` exactly: budgets, Family_Track, EV_Refund, Unused_Budget, flags, the 2026 payroll parameters with their `source` URLs, and the `pending` list
    - `tests/rules/test_rules_file.py`:
      - the tracked file loads;
      - each default of Req 3.1-3.15 has its expected value;
      - every `pending` item resolves to its current value
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.20_

  - [x] 3.4 Create the mock catalog and occupations files
    - `config/car_catalog.yaml`: the `mock_data` marker and 15 cars, chosen with a SQL query against the loaded dataset
      - registration year 2026, with every group A-D present;
      - at least 3 cars whose dataset Powertrain is electric;
      - exactly one `family_track: true` car, in group A;
      - quoted codes;
      - realistic monthly costs, some below their group budget, so Unused_Budget appears
    - `config/occupations.yaml`: the `mock_data` marker and a few occupations with Hebrew names, because employees type them in Hebrew
      - at least one with `car_waiver_allowed: true` and a Car_Maintenance amount;
      - at least one with `car_waiver_allowed: false`
    - `tests/rules/test_mock_files.py`, against the loaded real dataset:
      - 12-18 cars, with every group present;
      - at least 3 electric cars, judged by their dataset records;
      - one Family_Track car;
      - every key found in the dataset;
      - the occupation coverage above;
      - the marker in both files
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.10, 4.11, 4.13_

  - [x] 3.5 Add the catalog-versus-dataset check
    - `car_advisor/dataset/lookup.py`: a `CatalogMismatch` error and `check_catalog(lookup, catalog)`
      - It calls `missing_keys`.
      - It raises with the display name and full key of every missing car.
      - The lifespan calls it after the import (task 9.2).
    - `tests/dataset/test_catalog_check.py`:
      - the mock catalog passes against the real file;
      - a catalog with two unknown keys names both cars
    - _Requirements: 4.6, 4.7_

  - [ ]* 3.6 Write property test for the rules schema
    - **Property 7: The rules schema accepts exactly the valid configurations**
    - `tests/rules/test_config_properties.py`
    - **Validates: Requirements 3.7, 3.8, 3.12, 3.14, 3.15, 3.17, 3.23**

  - [ ]* 3.7 Write property test for catalog and occupation loading
    - **Property 8: Catalog and occupation files load at any size and report every invalid entry**
    - **Validates: Requirements 4.12, 4.15, 4.16**

  - [ ]* 3.8 Write property test for missing catalog cars
    - **Property 9: Every catalog car missing from the dataset is reported**
    - `tests/dataset/test_catalog_properties.py`
    - **Validates: Requirements 4.6, 4.7**

  - [ ]* 3.9 Write property test for the group-filtered catalog list
    - **Property 10: The catalog list holds the employee's group and lower groups**
    - Test `CarCatalog.cars_for` and the group labels here. Task 9.4 covers the response when no group is stored
    - **Validates: Requirements 4.8, 4.9, 4.17**

- [x] 4. Build the pure engine: money, eligibility and payroll
  - [x] 4.1 Implement display rounding
    - `car_advisor/engine/money.py`: `Amount(exact, shown)`, `display` (`quantize(Decimal(1), rounding=ROUND_HALF_UP)`), `amount` and `percent_of`
    - `tests/engine/test_money.py`:
      - 1234.50 → 1235, 1234.49 → 1234, −0.50 → −1 and −1234.50 → −1235;
      - `percent_of` is exact
    - _Requirements: 7.12_

  - [ ]* 4.2 Write property test for display rounding
    - **Property 22: Display rounding is half away from zero**
    - `tests/engine/test_money_properties.py`
    - **Validates: Requirements 7.12**

  - [x] 4.3 Implement Eligibility_Checker
    - `car_advisor/engine/copy_he.py`: the Hebrew group letters, a Hebrew label for every `MissingField`, and the eligibility reason templates from Calculation Details
      - Include one missing-data sentence that names every missing label.
      - Include separate wording for an occupation missing from the profile and one missing from the table.
    - `car_advisor/engine/eligibility.py`: `EligibilityResult` and `check_eligibility`, as in the design
      - Collect every missing input first: `car_group`, `powertrain`, `eligibility_group` (regular track) and `children_under_23` (Family_Track).
      - Decide only when nothing is missing: by group order index for the regular track, and by the children threshold for Family_Track.
      - Set `is_electric` only from `record.powertrain`.
      - For a waiver, match the occupation after `strip()`.
    - `tests/engine/test_eligibility.py`:
      - each branch returns its status and a reason with the values it used;
      - a car outside the catalog is `missing` with `car_group`;
      - an absent record is `missing` with `powertrain`;
      - a car named "EV" with a hybrid record is not electric;
      - an occupation missing from the profile and one missing from the table give different reasons
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 5.11, 5.12, 5.13, 5.14, 5.17, 5.18, 5.19, 5.20, 5.21, 5.22_

  - [ ]* 4.4 Write property test for eligibility decisions
    - **Property 11: Eligibility follows the configured rules**
    - `tests/engine/test_eligibility_properties.py`
    - **Validates: Requirements 3.18, 5.2, 5.5, 5.6, 5.9, 5.10, 5.12, 5.13**

  - [ ]* 4.5 Write property test for missing eligibility inputs
    - **Property 12: Missing inputs are listed completely and never decided**
    - **Validates: Requirements 5.1, 5.7, 5.8, 5.11, 5.14, 5.19, 5.20, 5.21, 5.22**

  - [x] 4.6 Implement Payroll_Calculator
    - `car_advisor/engine/payroll.py`: `PayrollInput`, `PayrollBreakdown`, `PayrollOk`, `PayrollMissing`, `PayrollInvalid` and `calculate_net_pay`. Follow steps 0-8 of Calculation Details exactly:
      - invalid inputs win over missing ones, and each list is complete, in Req 7.2 order;
      - both credits are returned as calculated, before the ₪0 income tax floor;
      - the employer study-fund excess counts only when the employee contributes
    - `tests/engine/test_payroll.py`, with values computed by hand:
      - taxable income exactly on a bracket edge, and ₪1 above it;
      - credits larger than the tax give ₪0 income tax;
      - income above the maximum insurable income adds no National Insurance or health tax;
      - both EV_Refund modes, and every flag on and off;
      - one missing input and one invalid input
    - _Requirements: 6.14, 6.15, 6.16, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 7.11, 7.13, 7.16_

  - [x] 4.7 Add the fictitious payroll fixtures
    - `tests/fixtures/payroll/`: four YAML files, one per fictitious employee:
      - group A, regular car;
      - group B, gives up the car (Car_Maintenance from an occupation that allows the waiver, Usage_Value 0);
      - group C, switches to an EV (EV_Refund ₪250);
      - group D, Family_Track (Participation is the employee's share of the gap)
    - Each file holds the profile, the scenario inputs and the expected value of every Req 7.13 item. Compute the expected values by hand, not with `payroll.py`, and show the working in comments
    - `tests/engine/test_payroll_fixtures.py` is parametrized over every YAML file in the directory and compares each item through `display()`. Adding a file needs no code change
    - _Requirements: 7.13, 7.15, 15.11_

  - [ ]* 4.8 Write property test for Participation as a net deduction
    - **Property 17: Participation is a net deduction**
    - `tests/engine/test_payroll_properties.py`
    - **Validates: Requirements 6.1, 6.14, 6.15, 6.16**

  - [ ]* 4.9 Write property test for missing and invalid payroll inputs
    - **Property 19: Payroll reports every missing and every invalid input**
    - **Validates: Requirements 7.3, 7.16**

  - [ ]* 4.10 Write property test against the payroll reference model
    - **Property 20: Payroll matches a reference model**
    - `tests/engine/reference_payroll.py` computes bracket tax from a cumulative "tax up to edge" table, written directly from Req 7.4-7.11
    - **Validates: Requirements 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.11**

  - [ ]* 4.11 Write property test for the contribution base
    - **Property 21: Contributions ignore items outside the base**
    - **Validates: Requirements 7.10**

- [ ] 5. Build the pure engine: comparison and explanation
  - [x] 5.1 Implement the Comparison_Engine scenarios
    - `car_advisor/engine/comparison.py`: `ComparisonInputs`, `MissingItem`, `TodayResult`, `AlternativeResult`, `Comparison` and `compare`
    - Calculate Today once per call:
      - with the current car: the record's Usage_Value, the current Participation, EV_Refund by Powertrain, and Car_Maintenance 0;
      - with no car: every car item is 0
    - Build each alternative's scenario from the design's scenario table:
      - **regular-track car:** Participation is the rep answer for the same `car_key`, or the current Participation if it is the current car; otherwise `new_car_participation` is missing;
      - **Family_Track car:** the gap and the employee share, with the employer share as the remainder. Car_Cost is `family_track.car_cost`;
      - **waiver:** Car_Maintenance comes from the occupation
    - Check in the design's order:
      1. eligibility, where `not_eligible` returns at once;
      2. missing inputs, each listed once, with `sent_to_rep` from `inputs.rep_questions`, plus Today's missing fields;
      3. the calculation, through `check_eligibility` and `calculate_net_pay` only
    - Unused_Budget appears only when the budget exceeds the Car_Cost, and it is never a payroll input
    - `tests/engine/test_comparison.py`:
      - Today with a car and without one;
      - a rep Participation applies only to its own car;
      - switching a column to a different car makes it `pending`;
      - a `pending` Today makes every alternative that is not `not_eligible` `pending`;
      - a `not_eligible` column carries only the car, the status and the reason
    - _Requirements: 6.1, 6.2, 6.3, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11, 6.12, 6.19, 6.21, 6.22, 8.1, 8.2, 8.3, 8.4, 8.5, 8.11, 8.12, 8.13, 8.14_

  - [x] 5.2 Add the breakdown attribution
    - `comparison.py`: compute N0..N4 by substituting Usage_Value, EV_Refund, Participation and Car_Maintenance in that order
      - each component's exact value is `N_k - N_(k-1)`, and its shown value is `display(N_k) - display(N_(k-1))`;
      - the exact and shown difference follow the design
    - `tests/engine/test_breakdown.py`: a hand-checked switch to an EV gives the expected components and difference, and both the exact and the shown parts sum to their difference
    - _Requirements: 8.6, 8.7_

  - [x] 5.3 Implement the Explanation
    - `copy_he.py`: the headline and component templates from the design's tables
    - `car_advisor/engine/explanation.py`: `build_explanation(difference_shown, components)`, with the design's layout rules
      - line 1 holds the headline and the first two non-zero components;
      - line 2 holds the rest;
      - the last line ends with `.`
    - Call it from `compare` for every calculated alternative
    - `tests/engine/test_explanation.py`:
      - each headline case;
      - one component of each sign;
      - four non-zero six-digit components keep each line within 120 characters;
      - with no non-zero component, the explanation is a single line
    - _Requirements: 8.8, 8.9, 8.10_

  - [x] 5.4 Write the challenge rule example tests
    - `tests/engine/test_rule_examples.py` has one named test per example, run through the engine with the tracked `rules.yaml`:
      - Family_Track 3,500 / 6,000 gives 1,250 / 1,250; 5,200 / 6,000 gives 400 / 400; 4,500 / 4,500 gives 0 / 0;
      - group D may choose a group C car, and group C may not choose a group D car;
      - an electric alternative adds ₪250 EV_Refund, while hybrid, plug-in and conventional ones add ₪0;
      - Unused_Budget is not paid: two alternatives that differ only in a Car_Cost below the budget give the same Net_Pay;
      - giving up the car is allowed for a permitted occupation and refused for a forbidden one
    - _Requirements: 15.9, 3.1, 3.3, 3.4, 3.5, 3.6, 5.5, 5.6, 5.12, 5.13, 6.6, 6.7, 6.11, 6.12_

  - [x] 5.5 Add the structure tests
    - `tests/test_structure.py`:
      - an `ast` scan asserts that every module in `car_advisor/engine/` imports only the standard library and `pydantic`;
      - a denylist scan asserts that none of 3500, 3700, 5200, 6000, 7010, 10060, 19000, 25100, 46690, 60130, 7703, 51910, 9700 and 15712 appears in any file under `backend/src/`
    - _Requirements: 3.19, 15.1, 15.2_

  - [ ]* 5.6 Write property test for electric detection and the EV_Refund
    - **Property 13: Electric means Powertrain electric, and only then is there an EV_Refund**
    - `tests/engine/test_comparison_properties.py`
    - **Validates: Requirements 3.18, 5.17, 5.18**

  - [ ]* 5.7 Write property test for engine purity
    - **Property 14: Engine functions are pure and deterministic**
    - **Validates: Requirements 5.3, 5.4, 7.1, 8.8**

  - [ ]* 5.8 Write property test for the Family_Track split
    - **Property 15: The Family_Track split sums to the gap**
    - **Validates: Requirements 6.6, 6.7, 6.8, 6.9**

  - [ ]* 5.9 Write property test for Unused_Budget
    - **Property 16: Unused_Budget is information only**
    - **Validates: Requirements 6.11, 6.12, 6.22**

  - [ ]* 5.10 Write property test for Participation sources
    - **Property 18: Participation comes only from its sources**
    - **Validates: Requirements 6.4, 6.5, 6.19, 6.21**

  - [ ]* 5.11 Write property test for column independence
    - **Property 23: Columns are independent and delegate to the checker and the calculator**
    - **Validates: Requirements 8.2, 8.4, 8.13**

  - [ ]* 5.12 Write property test for the telescoping breakdown
    - **Property 24: The breakdown telescopes to the difference**
    - **Validates: Requirements 8.3, 8.6, 8.7**

  - [ ]* 5.13 Write property test for the Explanation
    - **Property 25: The Explanation agrees with the table**
    - `tests/engine/test_explanation_properties.py`
    - **Validates: Requirements 8.9, 8.10**

  - [ ]* 5.14 Write property test for status gating
    - **Property 26: Status gates what is returned**
    - **Validates: Requirements 8.11, 8.12, 8.14**

- [x] 6. Checkpoint - Ensure all engine tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - `./check.sh` exits 0, and the four payroll fixtures and every rule example pass.

- [x] 7. Build the conversations layer
  - [x] 7.1 Implement profile validation and the script steps
    - `car_advisor/conversations/__init__.py` and `car_advisor/conversations/profile.py`
    - `ProfileValidationError` lists every invalid field with its Hebrew allowed-range message from `copy_he.py`, for example "a percentage between 0 and 100"
      - Validate the merged change set with `EmployeeProfile`.
      - Map each Pydantic error to its field by `loc[0]`.
    - `ScriptStep(number, missing_fields)` and `script_step(profile, open_fields)`:
      - the four steps follow the challenge script;
      - a step is skipped when its only missing fields have an open Rep_Question
    - `tests/conversations/test_profile.py`:
      - the steps come in order;
      - several fields given at once satisfy every step they cover;
      - a stored field is never listed;
      - a step is skipped when its missing field has an open question;
      - each kind of invalid value gets its message
    - _Requirements: 9.1, 9.2, 9.16, 11.2, 11.3_

  - [x] 7.2 Implement the conversation service core
    - `car_advisor/conversations/service.py`: `ConversationNotFound`, `NewMessage`, `ConversationState` and these `ConversationService` methods:
      - `__init__`;
      - `create`, which stores the employee number `1000 + secrets.randbelow(9000)` and the profile `{}`;
      - `state`, which returns messages by `id`, profile, alternatives, comparison, open questions and agent status;
      - `append_message`
    - `tests/conversations/test_service.py`:
      - a created conversation's state is empty;
      - an unknown ID raises `ConversationNotFound`;
      - messages come back in insertion order;
      - a notice carries its code and no text
    - _Requirements: 10.14, 11.9, 11.10, 14.7_

  - [x] 7.3 Add profile updates, alternatives and recalculation
    - `service.py`:
      - `ComparisonView`, shaped as in the design's API section, with displayed integers, Hebrew labels and statuses;
      - `ProfileUpdate`;
      - `update_profile` (all-or-nothing) and `update_profile_partial` (saves the valid fields and returns the rejected ones with their allowed range);
      - `set_alternatives` and `replace_alternative`
    - The private recalculation runs in the same transaction as each mutation:
      1. load the records with `get_many`;
      2. read this conversation's rep questions and answered participations;
      3. call `compare`;
      4. store the `ComparisonView` JSON
    - On `StaleDataError`, re-read and re-apply once. If it fails again, raise a conflict error
    - `tests/conversations/test_recalculation.py`:
      - `update_profile` stores nothing when one field is invalid;
      - `update_profile_partial` stores the valid fields;
      - after each mutation, the stored comparison equals `compare()` on the stored state;
      - a concurrent version bump is retried once
    - _Requirements: 8.15, 9.16, 11.3, 11.4, 11.8, 11.10_

  - [x] 7.4 Add the run guard and the agent history
    - `service.py`:
      - `try_start_run`, a conditional `UPDATE` where `agent_run_started_at` is NULL or older than 90 s;
      - `finish_run`, `load_history`, and `save_turn`, which stores the history JSON and the reply in one transaction
      - `state` reports `agent.running` from the guard
    - `tests/conversations/test_run_guard.py`:
      - a second `try_start_run` fails while the guard is held;
      - a guard older than 90 s can be taken;
      - `finish_run` releases the guard;
      - `save_turn` appends one advisor message and stores the history
    - _Requirements: 10.10, 10.12, 14.7_

  - [x] 7.5 Implement Rep_Question creation and the rep list
    - `car_advisor/conversations/rep_questions.py`: `RepQuestionType`, `RepField`, `open_or_get` and `list_for_rep`
      - The question text comes from Hebrew templates in `copy_he.py`. It holds the employee label, the group, the car's display name with its Official_Key, and the occupation as recorded. It never holds salary data.
      - `open_or_get` inserts inside a savepoint. On `IntegrityError` it selects the existing open row.
      - `list_for_rep` returns `RepQuestionView` rows with only the Req 12.3 fields: open first, then answered, each by `created_at` ascending.
      - Recalculation marks the field as sent to the representative.
    - `tests/conversations/test_rep_questions.py`:
      - a second call returns the same ID with `created=False`;
      - the question text holds no profile amount;
      - the list order is right;
      - the employee label is a snapshot taken at creation
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 12.2, 12.3, 12.4, 12.5_

  - [x] 7.6 Implement answering a Rep_Question
    - `rep_questions.py`:
      - `ParticipationAnswer`: an amount of at least 0, a real calendar date and an optional note;
      - `TextAnswer`: text that is not blank;
      - `AnswerOutcome`: answered, `already_answered`, `not_found` or `invalid`
    - `answer` runs `UPDATE ... WHERE id = :id AND status = 'open'`. It then recalculates in the same transaction, so the Participation applies to the same `car_key`, with its effective date
    - `tests/conversations/test_answer.py`:
      - a valid answer is stored exactly and recalculates;
      - a second answer gets `already_answered` and changes nothing;
      - text sent to a participation question is `invalid`, and the question stays open;
      - a threaded race stores exactly one answer
    - _Requirements: 6.17, 6.18, 6.19, 10.5, 10.6, 10.7, 10.8, 10.9, 12.7_

  - [ ]* 7.7 Write property test for the stored comparison
    - **Property 27: The stored comparison always matches the current state**
    - `tests/conversations/test_conversation_properties.py`
    - **Validates: Requirements 8.15, 11.4**

  - [ ]* 7.8 Write property test for field-by-field profile validation
    - **Property 28: Profile updates are validated field by field**
    - Cover `update_profile` and `update_profile_partial` here. Task 9.6 covers the document confirm route
    - **Validates: Requirements 9.16, 11.3, 13.6, 13.12**

  - [ ]* 7.9 Write property test for the script step
    - **Property 29: The script step asks only for missing fields, in order**
    - **Validates: Requirements 9.1**

  - [ ]* 7.10 Write property test for one open Rep_Question per combination
    - **Property 31: At most one open Rep_Question per combination**
    - Include the threaded variant on SQLite
    - **Validates: Requirements 10.4**

  - [ ]* 7.11 Write property test for answering once
    - **Property 32: A Rep_Question is answered once**
    - At this level, `answer` returns answered exactly once. Task 9.5 checks that the resume is scheduled once
    - **Validates: Requirements 10.7, 10.8**

  - [ ]* 7.12 Write property test for answer validation
    - **Property 33: Answers are validated by type and stored exactly**
    - **Validates: Requirements 6.17, 10.5, 10.6, 12.7**

  - [ ]* 7.13 Write property test for the rep list order
    - **Property 37: The rep list is ordered**
    - **Validates: Requirements 12.2**

- [x] 8. Build the advisor agent
  - [x] 8.1 Implement the six agent tools
    - `tests/conftest.py`: set `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`
    - `car_advisor/logs.py`: a tool-call log helper with `tool`, `ok`, `duration_ms` and `conversation_id`
    - `car_advisor/agent/__init__.py` and `car_advisor/agent/tools.py`: `AdvisorDeps`, the `logged_tool` wrapper and the six tools with their result models. Results carry displayed integers and Hebrew labels only
      - `logged_tool` writes one log line per call, and turns an unexpected exception into `{"error": "<action>_failed", "action_label": "<Hebrew>"}`.
      - `lookup_car` calls `get` when codes are given, and `search` otherwise.
      - `list_catalog_cars` uses the stored group, and returns the missing field when there is none.
      - `check_eligibility` runs Eligibility_Checker on the stored profile.
      - `compare_alternatives` stores the list and returns the stored `ComparisonView`.
      - `ask_rep` calls `open_or_get` and returns the code-built `employee_notice` ("I'm missing {label}, I asked the representative", in the challenge's Hebrew wording), or the could-not-send sentence if storing fails.
      - `update_profile` calls `update_profile_partial`. It sets `current_car_key` only when the key exists in the dataset.
    - `tests/agent/test_tools.py`:
      - a failing tool returns the error shape and logs `ok=false` with no amounts;
      - `ask_rep` twice returns the same ID;
      - a storage failure in `ask_rep` returns the not-sent sentence;
      - `update_profile` ignores a key that is not in the dataset
    - _Requirements: 2.9, 2.10, 4.8, 4.17, 6.4, 9.3, 9.6, 9.7, 9.8, 9.16, 9.17, 10.1, 10.3, 10.16, 14.10_

  - [x] 8.2 Implement the number guard
    - `car_advisor/agent/number_guard.py`: `numbers_in`, `tool_numbers` and `check_reply`, as in the design
    - `tests/agent/test_number_guard.py`:
      - "₪12,345" matches 12345 from a tool result;
      - an unknown number raises `ModelRetry`, naming it;
      - a number from the employee's message counts only after a tool echoes it
    - _Requirements: 9.5_

  - [x] 8.3 Define Advisor_Agent
    - `car_advisor/agent/advisor.py`: the static English `INSTRUCTIONS`, the dynamic instruction and `build_advisor(model)`
    - The static instructions cover every rule the design lists:
      - Hebrew replies in a neutral tone, with no exclamation marks or emoji, using the combined feminine/masculine form;
      - never calculate, estimate, round or guess;
      - ask only the fields the dynamic instruction lists;
      - confirm a car only from `lookup_car` candidates;
      - "I don't know" calls `ask_rep`. An occupation missing from the profile is asked from the employee, and one missing from the table goes to `ask_rep`;
      - give the fixed reply to off-topic questions;
      - name the failed action when a tool errors
    - The dynamic instruction is rendered from the stored state: profile labels and values, the current `ScriptStep`, the open Rep_Questions and the alternatives
    - `build_advisor` registers the six tools, `check_reply` as output validator with `output_retries=2`, and `model_settings={"openai_reasoning_effort": "low"}`
    - `tests/agent/test_advisor.py`:
      - a `TestModel` smoke test: exactly six tools, valid schemas, and each tool runs against the SQLite services;
      - the dynamic instruction lists the current step's missing fields and no stored field
    - _Requirements: 5.15, 5.16, 9.1, 9.3, 9.4, 9.10, 9.13, 9.14, 15.12_

  - [x] 8.4 Implement the agent runner
    - `car_advisor/agent/runner.py`: `AgentBusy`, `NoticeCode`, and `AgentRunner` with `run_turn` and `run_with_note`, as in the design
      - The run guard comes first, then the employee message is stored.
      - With no agent, or on a 401 `ModelHTTPError`, it stores `agent_unavailable`.
      - On any other error, or after the 60 s timeout, it stores `agent_failed`.
      - The history stays unchanged on failure, and `finish_run` runs in `finally`.
      - `run_with_note` waits up to 90 s for the guard, stores no employee message, and stores its failure notice when the run fails.
    - `tests/agent/test_runner.py`:
      - no agent, a 401, another exception and a timeout (patched down) each store their notice code and leave the history unchanged;
      - a held guard raises `AgentBusy`;
      - `run_with_note` appends only the reply
    - _Requirements: 9.11, 9.15, 9.18, 10.10, 10.11, 10.15, 11.15_

  - [x] 8.5 Write the FunctionModel scenario tests
    - `tests/agent/scripted_model.py`: a helper that scripts tool calls and text from the incoming messages
    - `tests/agent/test_scenarios.py`:
      1. **Only missing fields:** for several stored subsets, the reply names every missing field of the current step and no stored field.
      2. **Rep question instead of a guess:** the employee does not know the participation in the new car. `ask_rep` is called, one open row exists, and the reply holds the `employee_notice` and no participation amount.
      3. **Numbers only from tools:** a first reply with an unknown number triggers `ModelRetry`, and every number in the final reply appears in the tool results.
      4. **Group B gives up the car:** `check_eligibility` with a waiver, then `compare_alternatives`. The reply states only the decision, the reason and the returned amounts.
      5. **The scripted EV scenario:** profile steps, then a confirmed current car, then an EV alternative with unknown participation, which calls `ask_rep`. The rep answers through `rep_questions.answer`, and `run_with_note` replies with the answered field and the comparison. The stored EV column is calculated with ₪250 EV_Refund.
    - _Requirements: 2.9, 6.4, 9.1, 9.2, 9.5, 9.6, 9.7, 9.8, 9.9, 9.12, 10.10, 10.11, 15.12_

  - [ ]* 8.6 Implement Document_Reader (Requirement 13, optional)
    - `car_advisor/agent/document_reader.py`: `DocumentExtraction`, `sniff_media_type` and `read_document`, as in the design
      - The reader is a tool-less `Agent(model, output_type=DocumentExtraction)` that receives `BinaryContent` under a 60 s `wait_for`.
      - The bytes are never logged or stored.
    - `tests/agent/test_document_reader.py`:
      - a `FunctionModel` with a partial extraction marks the absent fields as not detected;
      - the failure and timeout paths
    - _Requirements: 13.3, 13.4, 13.8, 13.9, 13.10, 13.11_

  - [ ]* 8.7 Write property test for the number guard
    - **Property 30: Replies contain only numbers that tools returned**
    - `tests/agent/test_agent_properties.py`
    - **Validates: Requirements 9.5**

  - [ ]* 8.8 Write property test for resumes
    - **Property 34: A resume only appends**
    - **Validates: Requirements 10.10, 10.15**

- [x] 9. Build the API
  - [x] 9.1 Define the API schemas
    - `car_advisor/api/__init__.py` and `car_advisor/api/schemas.py`
    - Request bodies:
      - message text of 1-2,000 characters that is not blank;
      - profile changes;
      - an alternative;
      - a rep answer that holds either a participation or a text
    - Response models from the design's API section, reusing `ComparisonView` and `EmployeeProfile`: the `ConversationState` payload, `CatalogCarView`, `CarCandidateView`, `RepQuestionView`, `Health`, and the error body `{code, fields?}`
    - `tests/api/test_schemas.py`:
      - blank and 2,001-character messages are rejected;
      - a rep answer with both shapes, or with neither, is rejected
    - _Requirements: 10.5, 10.6, 12.5, 12.7_

  - [x] 9.2 Implement the app factory and the startup lifespan
    - `car_advisor/api/app.py`: `Services`, `create_app(settings=None)`, `RequestLogMiddleware`, and the exception handlers:
      - `ConversationNotFound` → 404;
      - `ProfileValidationError` → 422, with per-field Hebrew errors;
      - `AgentBusy` and a conflict → 409;
      - database unavailable → 503
    - The lifespan follows the design's order:
      1. settings;
      2. `load_config`;
      3. `ensure_database`, then `run_migrations`;
      4. the import, only when `has_records` is false;
      5. `check_catalog`;
      6. `build_advisor` only when `OPENAI_API_KEY` is set. Otherwise log an `agent_disabled` warning
    - Each failure logs its `stage` and `error_type` and re-raises, so uvicorn exits non-zero. A settings failure logs variable names only
    - Keep each startup step a small function, so tests can build the app on SQLite with a test model
    - `tests/api/test_startup.py`:
      - a config error stops startup and logs the path and the problems;
      - the import runs only when the year has no records;
      - a missing catalog car stops startup;
      - with no API key, `agent.available` is false;
      - a settings error logs names only
    - _Requirements: 1.14, 1.15, 1.20, 3.16, 3.17, 3.18, 3.22, 4.6, 4.7, 14.3, 14.5, 14.11_

  - [x] 9.3 Implement the health route
    - `car_advisor/api/health.py`: `GET /api/health`
      - The database check runs in a thread under a 3 s `wait_for`.
      - It returns the `Health` body, with 503 when a check fails.
    - `tests/api/test_health.py`:
      - on SQLite it returns 200 with the record count;
      - with the database stubbed unreachable, it returns 503 with `database_reachable: false` within 5 s
    - _Requirements: 14.9_

  - [x] 9.4 Implement the employee routes
    - `car_advisor/api/conversations.py`:
      - `POST /conversations` returns 201;
      - `GET /conversations/{id}` returns 404 for an unknown or malformed ID, and creates nothing;
      - `POST /conversations/{id}/messages` runs `run_turn` and returns the state, or 409 `agent_busy`;
      - `PATCH /conversations/{id}/profile` uses `update_profile`. An invalid value gives 422 `invalid_fields`, and nothing is saved;
      - `PUT /conversations/{id}/alternatives/{position}`;
      - `GET /conversations/{id}/catalog` returns the group-filtered cars with group labels. With no group stored, it returns an empty list and `missing_field: "eligibility_group"`;
      - `GET /cars/search` returns 422 `missing_inputs` or 503 `lookup_failed` on failure
    - `tests/api/test_conversations.py`:
      - each success and error code;
      - with no API key, a message returns 200 with the `agent_unavailable` notice, and the profile and alternative routes still work;
      - a catalog request with no group stored;
      - one JSON log line per request and per tool call, with the Req 14.10 fields (`caplog`)
    - _Requirements: 2.13, 2.14, 4.8, 4.9, 4.17, 9.15, 10.10, 11.3, 11.7, 11.8, 11.9, 11.10, 11.11, 11.12, 14.10_

  - [x] 9.5 Implement the rep routes
    - `car_advisor/api/rep.py`: `GET /rep/questions` and `POST /rep/questions/{question_id}/answer`
      - The answer route stores the answer and recalculates.
      - Only an `answered` outcome schedules `run_with_note`, with the English answer note, as a `BackgroundTasks` job.
      - Errors: 404, 409 `already_answered`, and 422 `invalid_answer` with per-field errors.
    - `tests/api/test_rep.py`:
      - field exposure: the rep view holds only the ID, the Req 12.3 fields, the status and the answer, with no conversation ID and no salary field;
      - a second answer gets 409, and a stub runner records exactly one resume;
      - an invalid answer leaves the question open
    - _Requirements: 10.7, 10.8, 10.10, 10.11, 12.3, 12.4, 12.5, 12.7, 12.8, 12.9, 12.10_

  - [ ]* 9.6 Implement the document routes (Requirement 13, optional)
    - `car_advisor/api/documents.py`:
      - `POST /conversations/{id}/documents` runs the file checks before the model. Errors: 422 `invalid_file` or `nothing_detected`, 502 `reader_failed`, 504 `reader_timeout`. Nothing is stored;
      - `POST /conversations/{id}/documents/confirm` saves through `update_profile` (all-or-nothing), then schedules `run_with_note` to identify the confirmed car
    - `tests/api/test_documents.py`:
      - each error code;
      - confirm saves only the non-empty fields;
      - after a cancel, no candidate value is in the database
    - _Requirements: 13.1, 13.2, 13.5, 13.6, 13.7, 13.9, 13.11, 13.12_

  - [ ]* 9.7 Write property test for the upload gate (Requirement 13)
    - **Property 39: The upload gate runs before the model**
    - Use a counting stub reader. `tests/api/test_api_properties.py`
    - **Validates: Requirements 13.2**

  - [ ]* 9.8 Write property test for conversation reloads
    - **Property 35: A conversation reloads exactly as stored**
    - **Validates: Requirements 11.10**

  - [ ]* 9.9 Write property test for unknown conversation IDs
    - **Property 36: Unknown conversation IDs create nothing**
    - **Validates: Requirements 11.11**

  - [ ]* 9.10 Write property test for rep API privacy
    - **Property 38: The rep API exposes no private data**
    - **Validates: Requirements 12.4, 12.5, 13.10**

  - [ ]* 9.11 Write property test for logs and storage privacy
    - **Property 40: Nothing private reaches logs or storage**
    - **Validates: Requirements 13.9, 14.11**

- [x] 10. Checkpoint - Run the backend end-to-end against the SQL Server container
  - Ensure all tests pass, ask the user if questions arise.
  - With `.env` copied from `.env.example`, run `docker compose up -d db`. Then `uv run pytest -m mssql`, with `TEST_MSSQL_URL` set, passes.
  - In `backend/`, run `uv run uvicorn car_advisor.api.app:app --host 127.0.0.1` as a background process. The first start imports 77,988 records, and a restart skips the import.
  - `curl http://127.0.0.1:8000/api/health` returns 200 with 77,988 records. Creating a conversation and saving a profile through the API stores a comparison.
  - Stop the backend, then run `docker compose stop db`, which keeps the volume.

- [x] 11. Set up the Angular frontend foundation
  - [x] 11.1 Create the Angular 22 workspace
    - In `frontend/`, run `npx @angular/cli@22.2.2 new`: standalone, zoneless, strict, SCSS, no SSR, and the default Vitest test runner
    - `package.json`: exact pins for the Angular packages (22.2.2), typescript 6.0.3, vitest 5.0.4 and jsdom 30.1.2. Keep `package-lock.json`
    - `angular.json`: set `optimization.styles.inlineCritical: false`, and wire `proxy.conf.json` (forwarding `/api` to `http://127.0.0.1:8000`) into `ng serve`
    - `src/index.html`: `<html lang="he" dir="rtl">` with a Hebrew title
    - `app/app.config.ts`: `provideRouter(routes, withComponentInputBinding())` and `provideHttpClient()`
    - `app/app.ts` is a router-outlet shell. `app/app.routes.ts` starts empty, and tasks 12.1 and 13.1 add the routes
    - Remove the generated sample content
    - Verify: `npx ng build` and `npx ng test --watch=false` pass
    - _Requirements: 15.4, 16.1_

  - [x] 11.2 Add ESLint and Prettier
    - Exact pins: angular-eslint 22.6.0, eslint 10.12.0 and prettier 3.9.10
    - Add `eslint.config.js` and `.prettierrc`, plus the `package.json` scripts `lint`, `format` and `format:check`
    - Verify: `npm run lint` and `npm run format:check` pass
    - _Requirements: 15.4, 15.5, 15.7_

  - [x] 11.3 Add the design tokens, base styles and the font
    - `src/styles/_tokens.scss`:
      - neutral grays and one accent, `--color-accent: #1d4f91`;
      - status colors: increase `#1a6b3c`, decrease and error `#b42318`;
      - placeholder `#5f6368`;
      - the focus ring, 4 px radii, 1 px borders and a spacing scale
    - `src/styles/_base.scss`: Heebo on all text, RTL defaults, the visible focus ring, a visually-hidden utility and a `tabular-nums` helper
    - Also `_forms.scss`, `_table.scss`, and `styles.scss`, which imports `@fontsource-variable/heebo` 5.3.0 (exact pin)
    - Use no gradients, shadows, blur or component library
    - Verify:
      - `npx ng build` emits the Heebo woff2 files;
      - no built CSS file references an external `http(s)://` host
    - _Requirements: 16.4, 16.6, 16.7, 16.11, 16.13_

  - [x] 11.4 Implement the typed API client
    - `app/shared/api-models.ts`: hand-written mirrors of `api/schemas.py`
    - `app/shared/api.ts`: `ApiClient`, with one method per route. Each returns a `Promise` through `firstValueFrom`
    - `app/shared/api.spec.ts`, with `HttpTestingController`:
      - each method uses the right HTTP method and URL;
      - an error status rejects with the parsed `{code, fields}` body
    - _Requirements: 11.9, 11.10, 15.4_

  - [x] 11.5 Implement the formatting helpers
    - `app/shared/format.ts`:
      - `formatShekels`: `₪12,345`, with U+2212 for negatives;
      - `formatSigned`: `+₪210`, `−₪120` or `₪0`;
      - `formatDate`: `dd/mm/yyyy`;
      - `groupLetter`: A→א, B→ב, C→ג, D→ד
    - `app/shared/format.spec.ts`: these examples, zero, and a seven-digit amount
    - _Requirements: 16.1, 16.3, 16.5_

  - [x] 11.6 Implement bidi isolation
    - `app/shared/bidi-text.ts`: `<app-bidi-text [text]>`
      - It splits the text with `/[+\u2212-]?₪?\d[\d,./-]*/g`.
      - It renders each match in a `<bdi dir="ltr">`, through interpolation only, never `innerHTML`.
    - `app/shared/bidi-text.spec.ts`:
      - an amount, a date and an Official_Key inside Hebrew text each end up in a `<bdi>`;
      - the rendered text equals the input;
      - HTML in the input renders as text
    - _Requirements: 16.2_

  - [x] 11.7 Implement polling and the shared state copy
    - `app/shared/poller.ts`: `pollWhile(condition, intervalMs, tick)` and `POLL_INTERVAL_MS = 3000`
    - `app/shared/shared.copy.ts`: the loading, empty and failed messages
    - `app/shared/poller.spec.ts`, with `vi.useFakeTimers()`:
      - it ticks every 3 s while the condition holds;
      - it stops when the condition turns false, and on destroy
    - _Requirements: 10.12, 10.13, 16.14_

  - [ ]* 11.8 Write property-style tests for formatting and bidi isolation
    - **Property 41: Money formatting and bidi isolation**
    - `app/shared/format.property.spec.ts`: a seeded loop of 200 generated values per test, with no new dependency
    - **Validates: Requirements 16.2, 16.3, 16.5**

- [x] 12. Build Employee_Screen
  - [x] 12.1 Implement the page, the store and the routes
    - `app/employee/employee.copy.ts` and `app/employee/employee-store.ts`: `EmployeeStore` with the `state`, `sending` and `notice` signals and the methods `send`, `saveField` and `replaceCar`
    - `app/employee/employee-page.ts|html|scss`:
      - `/employee` creates a conversation, then calls `router.navigate(['/employee', id], { replaceUrl: true })`;
      - `/employee/:conversationId` loads the stored state. A 404 shows "not found" and a "New conversation" button;
      - a "New conversation" action is always available;
      - from 1024 px up, the layout is a two-column grid with the 320 px sticky panel on the right. Below 1024 px it stacks chat, details and table;
      - the screen and each area have headings
    - `app/app.routes.ts`: `/` redirects to `/employee`, plus `/employee` and `/employee/:conversationId`
    - `employee-page.spec.ts`:
      - with no ID, it creates a conversation and navigates with `replaceUrl`;
      - with an ID, it loads the state;
      - a 404 shows "not found", and the button creates a new conversation
    - _Requirements: 11.1, 11.9, 11.10, 11.11, 11.12, 16.8, 16.9, 16.10_

  - [x] 12.2 Implement the chat
    - `app/employee/chat/`: `chat.ts`, `message-list.ts`, `composer.ts` and `typing-indicator.ts`
      - Messages render in an `<ol>` through `BidiText`. Notices are rendered from `employee.copy.ts` by their code.
      - A visually hidden `aria-live="polite"` region announces each new advisor message once. Notices use a separate `aria-live="assertive"` region.
      - The composer is a `<form>` with a visible `<label>` and a `<textarea>`. Enter sends and Shift+Enter adds a line.
      - The send button is disabled while `sending()` or `agent.running` is true.
      - The typing indicator shows from send until the reply or a notice.
    - `chat.spec.ts`:
      - Enter sends, and Shift+Enter does not;
      - the button is disabled while a run is active;
      - a new advisor message is announced once, and focus does not move;
      - a notice renders in the assertive region
    - _Requirements: 9.11, 9.15, 9.18, 10.15, 11.13, 11.14, 11.15, 16.2, 16.12, 16.16_

  - [x] 12.3 Implement the My Details fields and validation
    - `app/employee/my-details/`: `my-details.ts` and `details-field.ts`
      - Every Employee_Profile field shows in script order, filled or empty, as a label/input pair.
      - The group is a native `<select>` with Hebrew letters. Numeric fields are text inputs with `inputmode`, and "has a current car" is a yes/no select.
      - Signal Forms schemas mirror the server constraints. A field saves on blur or Enter.
      - An invalid value is not sent. Its field gets `aria-invalid="true"` and an `aria-describedby` that points at the Hebrew error. A server 422 shows the same way.
      - A failed save keeps the typed value and shows "not saved".
    - `my-details.spec.ts`:
      - group E, −1 amount, 101 %, −1 credit points, 2.5 children and "abc" are each rejected, with the error next to the field and no API call;
      - one valid value is saved
    - _Requirements: 11.2, 11.3, 11.4, 11.17, 16.15_

  - [x] 12.4 Add pay components and the current-car search
    - `my-details.ts`:
      - pay components are a list of kind, label and amount, with add and remove buttons;
      - the current car shows its name and Official_Key;
      - "Change" opens an inline search by registration year and text through `GET /api/cars/search`. Candidates are a radio group with the Req 2.6 details, and choosing one saves `current_car_key`
    - `my-details.spec.ts`:
      - adding or removing a component saves the list;
      - choosing a candidate saves its key;
      - a search with no match shows the none-found message
    - _Requirements: 2.6, 2.7, 2.8, 2.9, 11.2, 11.4_

  - [x] 12.5 Implement the comparison table
    - `app/employee/comparison/comparison-table.ts`: a semantic `<table>` with a `<caption>`, `<th scope="col">` for Today and each alternative, and `<th scope="row">` for each Req 8.5 item, in order
      - Numbers use tabular figures, aligned to the left edge, with `formatShekels` and `formatSigned`.
      - A `pending` or `not_eligible` column shows the status, the reason, and each missing field tagged "sent to the representative" or "not sent". It shows no net pay, difference, breakdown or explanation.
      - A `pending` Today shows its missing fields.
      - Unused_Budget shows its note, and a rep-provided Participation shows its effective date.
    - `comparison-table.spec.ts`: the cases in the design's frontend test list
    - _Requirements: 6.13, 6.20, 8.5, 11.5, 11.6, 11.16, 15.13, 16.3, 16.4, 16.5, 16.10_

  - [x] 12.6 Implement the car picker
    - `app/employee/comparison/car-picker.ts`: a native `<select>` in each car column header
      - It lists the cars from `GET /api/conversations/{id}/catalog` as "display name · group letter".
      - A change calls `PUT .../alternatives/{position}`, and the table updates from the response.
      - On failure, the previous car stays selected and "not saved" shows.
      - With no group stored, it is disabled, with a message next to it.
    - `car-picker.spec.ts`:
      - a change sends the right position and key;
      - a failure reverts the selection;
      - with no group, the picker is disabled
    - _Requirements: 4.9, 11.7, 11.8, 11.17, 11.18_

  - [x] 12.7 Wire polling and the area states
    - `employee-store.ts`: `needsPolling` (an open Rep_Question or `agent.running`) drives `pollWhile`. A failed poll keeps the last state and shows a one-line message until the next success
    - Every area shows the loading, empty and failed messages from `shared.copy.ts`
    - Every control can be reached and operated with the keyboard, and shows the focus ring
    - `employee-store.spec.ts`, with fake timers:
      - it polls every 3 s while a question is open, and stops once it is answered;
      - a failed poll keeps the state
    - _Requirements: 10.12, 10.13, 16.11, 16.14_

  - [ ]* 12.8 Implement document upload (Requirement 13, optional)
    - `app/employee/document-upload/`: `upload-button.ts` and `confirm-form.ts`, as in the design, plus the store methods `upload`, `confirmDocument` and `cancelDocument`
    - `upload-button.spec.ts` and `confirm-form.spec.ts`:
      - a wrong extension, or a file over 10,485,760 bytes, is rejected before upload;
      - the control is disabled while reading;
      - "Confirm" posts only the non-empty values;
      - a 422 keeps the form open, with errors next to the fields;
      - "Cancel" clears the candidates
    - _Requirements: 13.1, 13.2, 13.5, 13.6, 13.7, 13.12, 13.13_

  - [ ]* 12.9 Write property-style test for polling
    - **Property 42: Employee_Screen polls only while needed**
    - `employee-store.property.spec.ts`: a seeded loop over generated states
    - **Validates: Requirements 10.12, 10.13**

- [ ] 13. Build Rep_Screen
  - [x] 13.1 Implement the rep page
    - `app/rep/rep.copy.ts` and `app/rep/rep-page.ts|html|scss`:
      - it loads `GET /api/rep/questions`, then reloads every 3 s through `pollWhile` and on "Refresh";
      - it shows an "Open" group and an "Answered" group. When nothing is open, the open group shows a one-sentence message;
      - the list uses `@for (q of questions(); track q.id)`, so a reload keeps the DOM nodes;
      - a failed reload keeps the last list and shows "could not update" until the next success;
      - it reuses the Employee_Screen tokens and base styles
    - `app/app.routes.ts`: add `/rep`
    - `rep-page.spec.ts`:
      - the groups and their order;
      - the empty-group message;
      - a reload every 3 s, with fake timers;
      - a failed reload keeps the list and shows the message
    - _Requirements: 12.1, 12.2, 12.10, 12.11, 12.12, 12.14, 16.14_

  - [x] 13.2 Implement the question view
    - `app/rep/rep-question.ts` shows the Req 12.3 fields:
      - question text, type, and the creation date and time;
      - the employee label and the group letter;
      - the car name with its Official_Key, through `BidiText`;
      - the status
    - An answered question shows its stored answer read-only, with no form
    - `rep-question.spec.ts`:
      - an open question shows the answer form;
      - an answered question shows the answer and no form
    - _Requirements: 10.9, 12.3, 12.4, 12.9, 16.2_

  - [x] 13.3 Implement the answer form
    - `app/rep/answer-form.ts`:
      - a participation question gets an amount (`inputmode="decimal"`, at least 0), a required effective date (`<input type="date">`, a real calendar date) and an optional note;
      - every other type gets a required free-text field;
      - Signal Forms validation ties each error to its field;
      - the values live in a `Map<questionId, signal>` owned by `RepPage`, so a reload never resets them
    - Submit results:
      - 200: a confirmation in an `aria-live` region;
      - 409: an "already answered" message, then the read-only answer;
      - a network or other error: the values stay, with "not saved, try again"
    - `answer-form.spec.ts`:
      - it rejects a missing amount, a negative amount, a missing date and blank text;
      - it accepts a valid participation answer;
      - it keeps the typed values when the list reloads;
      - it handles a 409
    - _Requirements: 6.17, 6.18, 10.5, 10.6, 10.8, 12.6, 12.7, 12.8, 12.13, 15.13, 16.12, 16.15_

- [x] 14. Package the app with Docker Compose
  - [x] 14.1 Write the backend Dockerfile
    - `backend/Dockerfile`:
      - starts from `python:3.12-slim` and installs `libltdl7 libkrb5-3 libgssapi-krb5-2`;
      - copies `uv` from `ghcr.io/astral-sh/uv:0.12.23`;
      - uses the working directory `/app/backend` and runs `uv sync --frozen --no-dev`;
      - starts `uvicorn car_advisor.api.app:app --host 0.0.0.0 --port 8000 --no-access-log`
    - `backend/.dockerignore` excludes `.venv/` and the test and tool caches
    - Verify: `docker build backend` succeeds
    - _Requirements: 14.1_

  - [x] 14.2 Write the frontend Dockerfile and the nginx config
    - `frontend/Dockerfile`: a `node:22-alpine` build stage (`npm ci`, then `npx ng build`) and an `nginx:stable-alpine` runtime. Pin both base images by digest
    - `frontend/nginx.conf`:
      - serves the build with `try_files $uri /index.html`;
      - proxies `location /api/` to `http://backend:8000` with `proxy_read_timeout 90s`;
      - sets `client_max_body_size 11m`;
      - sends `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; font-src 'self'; connect-src 'self'`
    - `frontend/.dockerignore` excludes `node_modules/`, `dist/` and `.angular/`
    - Verify: `docker build frontend` succeeds
    - _Requirements: 14.12, 16.6_

  - [x] 14.3 Complete `compose.yaml`
    - Add the `backend` and `frontend` services exactly as in the design:
      - `env_file: .env`, with `DB_HOST: db`;
      - read-only bind mounts of `config/` and `data/`;
      - `depends_on` with `condition: service_healthy` on `db`;
      - ports `127.0.0.1:8000:8000` and `127.0.0.1:8080:80`
    - `tests/test_compose.py`:
      - every published port binds to `127.0.0.1`;
      - `backend` waits for a healthy `db`;
      - the `config/` and `data/` mounts are read-only
    - Verify: `docker compose config --quiet` succeeds
    - _Requirements: 3.18, 4.12, 14.1, 14.2, 14.15, 14.16_

  - [x] 14.4 Finish `check.sh`
    - Add the frontend steps in `frontend/`: `npm run lint`, `npm run format:check` and `npx ng test --watch=false`
    - Check that `uv` and `npm` exist first. If either is missing, exit non-zero with a clear message
    - Verify:
      - `./check.sh` exits 0;
      - it exits non-zero when one frontend test is deliberately broken. Revert the test afterwards
    - _Requirements: 15.4, 15.5, 15.14_

- [ ] 15. Write the documentation
  - [x] 15.1 Write `README.md`
    - In English, it covers:
      - what the project is, and the prerequisites: Docker with Compose, plus uv 0.12.23, Python 3.12 and Node 22.22 or later for dev mode;
      - the quick start: `cp .env.example .env`, set `DB_PASSWORD` and optionally `OPENAI_API_KEY`, then `docker compose up`;
      - the URLs `http://127.0.0.1:8080/employee`, `http://127.0.0.1:8080/rep` and `http://127.0.0.1:8000/api/health`;
      - dev mode: the database in Docker, `uv run uvicorn ... --reload`, and `npx ng serve` with the proxy;
      - the manual import command, the dataset path `data/usage-values-2026.csv`, the check command `./check.sh`, and a link to `docs/explanations.he.md`
    - `tests/test_docs.py` asserts that the README names `./check.sh`, `docker compose up` and both screen URLs
    - _Requirements: 15.5, 17.1, 17.12_

  - [x] 15.2 Write the Explanations_File: architecture and parts
    - `docs/explanations.he.md`, in Hebrew:
      - an architecture diagram (Mermaid) with the Database, Backend_API, Advisor_Agent, Frontend, the language model provider and the config files;
      - the folder structure;
      - how each part works, with its inputs, outputs and file location: Importer, Car_Lookup, the config loader, Eligibility_Checker, Payroll_Calculator, Comparison_Engine, Advisor_Agent and its six tools, the Rep_Question flow and the resume, Employee_Screen, Rep_Screen and Document_Reader
    - _Requirements: 17.2, 17.3_

  - [x] 15.3 Explain the calculation with a worked example
    - `docs/explanations.he.md`:
      - the Net_Pay steps, in the order of Requirement 7;
      - a worked example with numbers taken from the group C EV fixture in `tests/fixtures/payroll/`, including the breakdown and the Explanation;
      - only the monthly method is implemented, and locality-based tax benefits are out of scope;
      - the rounding rule
    - _Requirements: 7.14, 17.4_

  - [x] 15.4 Document running, settings, debugging, extending and the dataset quirks
    - `docs/explanations.he.md`:
      - running with `docker compose up` and in dev mode;
      - every App_Settings variable and every Rules_Config field, with its meaning, default and source. Settings and mock-file changes apply after `docker compose restart backend`;
      - debugging: the logs and their `duration_ms` and `stage` fields, `/api/health`, querying the Database, reading `agent_history`, running one test, `pytest -m mssql`, the live latency check and the manual accessibility review;
      - adding a feature, with an example for each Req 17.7 case;
      - each dataset quirk and how the code handles it: windows-1255, padded fields, leading zeros, the wrong EV legend and the roughly 12 miscoded rows, ambiguous trade names, the required vehicle type code, day-first dates, and Usage_Value taken as-is
    - `tests/test_docs.py`: every `Settings` variable name and every dotted `rules.yaml` key appears in `docs/explanations.he.md`
    - _Requirements: 14.6, 17.5, 17.6, 17.7, 17.8, 17.14_

  - [x] 15.5 Write the submission summary and the open questions
    - The submission summary has three parts:
      - **what works;**
      - **limitations**, including every Req 17.10 item: no login on either screen, fictitious salary data, mock catalog and occupations, the monthly method only, and the known resume gap;
      - **pilot needs**, including every Req 17.11 item: SSO or Active Directory, roles, an audit log, a retention policy, a least-privilege SQL login, a data-processing review for OpenAI, Strauss sign-off on the payroll method and golden tests, a security review and hosting
    - The open questions for Strauss list:
      - every `pending` item in `rules.yaml`, and every row of the design's "Pending from Strauss" table, including the request to add the vehicle type code to each catalog car;
      - the requirement contradictions that the design lists;
      - the license question: add MIT only if the hackathon requires it
    - `tests/test_docs.py`: every `pending` field of `rules.yaml` appears in the open questions
    - _Requirements: 4.14, 17.9, 17.10, 17.11, 17.13_

- [ ] 16. Final verification
  - [x] 16.1 Run a clean Compose start
    - Run `docker compose down -v`, then `cp .env.example .env`, and set `DB_PASSWORD` and `OPENAI_API_KEY`
      - Ask the user for the API key. Never print it or commit it.
    - Run `docker compose up --build -d`
    - Verify:
      - `/api/health` returns 200 with 77,988 records;
      - `docker compose logs backend` shows JSON lines with no amounts and no secrets;
      - `docker compose restart backend` keeps the data and skips the import;
      - `ss -ltn` shows the app's ports bound only to `127.0.0.1`
    - _Requirements: 14.1, 14.2, 14.7, 14.9, 14.10, 14.11, 14.15, 14.16_

  - [~] 16.2 Run the challenge script end-to-end
    - At `http://127.0.0.1:8080/employee`, follow the challenge script:
      1. the profile steps, then the current car identified from confirmed candidates;
      2. an EV alternative whose participation the employee does not know. Check the "I asked the representative" sentence and the `pending` column;
      3. answer on `/rep` with an amount and an effective date;
      4. the conversation resumes by itself, with a reply and a calculated EV column: ₪250 EV_Refund, the breakdown and the Explanation
    - In the logs, check each turn's `duration_ms` against the 8 s target, and check that the full script takes about a minute
    - Then ask the six questions of the challenge's sheet "עובדים לדוגמה" in the same way, answering the participation question with the car's amount from the catalog. Each net figure and each difference equals the sheet "חישובים" to the shekel
    - _Requirements: 9.11, 9.12, 10.10, 10.11, 10.12, 10.13, 11.5, 12.8_

  - [~] 16.3 Run the group B waiver scenario
    - A group B employee whose occupation allows the waiver asks to give up the car
      - The reply gives the decision and the reason.
      - The table shows Car_Maintenance and the Net_Pay difference.
    - With a forbidden occupation, the column is `not_eligible`
    - _Requirements: 5.12, 5.13, 9.9_

  - [x] 16.4 Check that a refresh keeps the state
    - Refresh the screen during the scenarios and after them. The messages, panel, alternatives and comparison reload as stored
    - An unknown ID shows "not found" and creates nothing
    - A My Details edit and a car picker change update the table without an agent turn
    - _Requirements: 11.4, 11.8, 11.10, 11.11_

  - [x] 16.5 Run the full check offline
    - `./check.sh` exits 0 with `OPENAI_API_KEY` empty and with no network, for example under `unshare -rn ./check.sh`
    - _Requirements: 15.5, 15.14_

  - [~] 16.6 Remove the handoff folder
    - Once 16.1-16.5 pass, ask the user to confirm, then delete `.kiro/handoff/`. The folder is git-ignored, so the deletion cannot be undone
    - Verify: `.kiro/handoff/` no longer exists
    - _Requirements: 14.13_

- [ ] 17. Apply the updated challenge data (October 2026)
  - Requirement 18 and the design section "Update: October 2026 Challenge Data" describe the change. Do this task before 16.2 and 16.3, because the live runs must use the new data.
  - `מעודכן/` holds the updated PDF, the Excel workbook and the rules JSON. Copy the data into the repository's own files. Nothing reads that folder at run time.
  - Every sub-task ends with the tests of its area passing. `tests/test_docs.py` compares the documentation with the rules and the engine, so it fails from 17.1 until 17.8 updates the documentation. `./check.sh` runs everything again in 17.9.

  - [x] 17.1 Load the challenge's payroll parameters and simplify the rules schema
    - `config/rules.yaml`:
      - the tax brackets, the National Insurance and health tax threshold and ceiling, and the pension credit ceiling of Req 18.6, with a `source` text that names the challenge's rules file `mock-2026-10-12` and says that its tables are the 2025 ones (the study fund keeps its public source);
      - remove `family_track.car_cost`;
      - rename the flag to `variable_pay_in_contribution_base` and set it to `true`;
      - set `payroll.study_fund.employer_rate_percent` to 0, with a comment that the challenge's method adds no employer deposit;
      - keep `ev_refund.mode: net`, with a comment that it gives the employee the exact ₪250 of the challenge's grossed-up refund;
      - `pending` holds `ev_refund.mode`, `payroll.credit_point.value` and `payroll.study_fund.employer_rate_percent`
    - `car_advisor/engine/config.py`: remove `FamilyTrack.car_cost`, rename the flag, and drop `_REQUIRED_PENDING` (the check that each listed default equals the current value stays)
    - `car_advisor/engine/payroll.py`: with the flag on, the contribution base adds the bonuses next to the commissions
    - `car_advisor/engine/comparison.py`: the Car_Cost of a Family_Track car is the car's catalog `monthly_cost`
    - Update the tests that name the old values:
      - `tests/engine/test_config.py` and `tests/rules/test_loader.py` (the schema);
      - `tests/rules/test_rules_file.py` (the new values; each `source` names the challenge's file; the three pending items; the test that switches the family-track cost is removed);
      - `tests/engine/test_payroll.py` (the flag name, and a bonus joins the base);
      - `tests/engine/test_rule_examples.py` and `tests/engine/test_comparison.py` (the family-track cost comes from the car);
      - the `DENYLIST` of `tests/test_structure.py` (16150, 22440, 7522, 50695 and 9500 replace 19000, 25100, 7703, 51910 and 9700)
    - Recompute the expected values of the four files in `tests/fixtures/payroll/` for the new parameters, by hand and not with `payroll.py`, with the working in the comments
    - Verify: `uv run pytest tests/engine tests/rules` passes
    - _Requirements: 18.6, 18.7, 18.9, 18.10, 18.11, 18.15_

  - [x] 17.2 Load the challenge's catalog and occupations
    - `car_advisor/engine/config.py`:
      - `CatalogCar.default_participation`, an optional amount of 0 or more, so that a catalog without it still loads;
      - `OccupationsTable.find` also matches the two forms of a slash name: "מפתח/ת תוכנה" is "מפתח תוכנה" and "מפתחת תוכנה", after `strip()`
    - `config/car_catalog.yaml`: the 20 cars of the sheet "קטלוג רכבים" (18 in groups and 2 family-track), in the sheet's order
      - an English display name from the make and the trade name, for example "Toyota Corolla Cross";
      - the key: tax year 2026, the sheet's registration year, vehicle type code "1", and the sheet's quoted manufacturer and model codes;
      - `monthly_cost` from "עלות חודשית לחברה" and `default_participation` from "השתתפות עובד ברירת מחדל";
      - the groups א-ד become A-D, and the two "משפחתי" cars are `family_track: true` in group A, with a comment that group A shows them to everyone;
      - the `mock_data` marker states that the costs and participations are fictitious and that the codes and usage values are real
    - `config/occupations.yaml`: the 14 occupations of the sheet "עיסוקים", with the names exactly as written (slash forms included), `car_waiver_allowed` from "מותר לוותר" and `car_maintenance` from the maintenance column (0 for the four mandatory-car occupations). Update the marker and the comment about matching
    - `.env.example` and the comments of the three settings: the files are the challenge's data, to be replaced by Strauss's files
    - Tests:
      - `tests/rules/test_data_files.py` (renamed from `test_mock_files.py`): 20 cars, 18 of them in A-D with every group present, 2 family-track cars, at least 3 electric cars by their dataset records, every key in the dataset, 14 occupations with both kinds, and the markers;
      - `tests/engine/test_config.py`: the slash forms match, and a name without a slash still needs the exact name;
      - `tests/dataset/test_catalog_check.py`: the shipped catalog passes against the real file;
      - the tests that name the old mock cars switch to the new ones: the old group C Tesla Model 3 becomes the BYD Atto 3 (group C, electric), the Corolla Hybrid becomes the Kia Niro (group B, hybrid), the Picanto and the Tucson keep their names with their 2025 keys, and the Ioniq becomes the BYD Seal (group D, electric). The files are `tests/agent/test_tools.py`, `test_runner.py`, `test_scenarios.py`, `test_advisor.py`, `tests/conversations/test_recalculation.py`, `test_rep_questions.py`, `tests/api/test_rep.py`, `tests/test_mssql_smoke.py`, and the costs and usage values in their expectations
    - Verify: `uv run pytest` passes except `tests/test_docs.py`, which task 17.8 updates
    - _Requirements: 18.1, 18.2, 18.3, 18.4_

  - [x] 17.3 Count the participation of a family-track car, and the maintenance of Today without a car
    - `car_advisor/engine/comparison.py`:
      - a Family_Track car takes its Participation like a regular-track car (the rep answer for the same car; `new_car_participation` is missing until then), and the employee's share of the gap is added to it for the payroll input;
      - `AlternativeResult` returns `participation` and `employee_gap_share` as two amounts, next to `employer_gap_share`;
      - Today with no car takes the occupation's `car_maintenance`. An unknown occupation makes Today `pending` with the field `occupation`, and every Alternative `pending` with it
    - `car_advisor/conversations/service.py` and `car_advisor/api/schemas.py`: `employee_gap_share` in the alternative view
    - The breakdown keeps its four items. Its participation item holds the change of the Participation and of the share together
    - Tests:
      - `tests/engine/test_comparison.py`: a family car with and without a rep answer, a gap of 0, the two shares and the participation as three amounts, the sum in the net pay, Today with no car for an occupation that allows the waiver and one that does not, and an unknown occupation;
      - `tests/engine/test_breakdown.py` and `tests/conversations/test_recalculation.py`: the family column and Today with no car
    - _Requirements: 18.8, 18.12_

  - [x] 17.4 Add the six challenge employees as golden tests
    - `tests/fixtures/challenge/employees.yaml`: for E-001 to E-006 the profile, the current car or the waiver, the alternative, and the reference values of the sheet "חישובים": net Today, net of the Alternative, the difference, the income tax of both, and National Insurance plus health tax of both. The profile lists the occupation, so that E-003 has its Today maintenance
    - `tests/engine/test_challenge_employees.py`:
      - runs each employee through `compare()` with the shipped config files and the real dataset, with the default Participation of the new car as the rep answer, and checks the nets and the difference;
      - runs each employee through `calculate_net_pay()` and checks the tax and National Insurance plus health tax of Today, and of the two Alternatives without an EV_Refund (E-002 and E-005);
      - E-002 checks the gap shares 1,150 and 1,150, and E-003 checks Today with no car
    - Verify: all 12 nets equal the sheet after rounding to whole shekels
    - _Requirements: 18.16_

  - [x] 17.5 Give the agent the occupation names
    - `car_advisor/agent/advisor.py`: the instructions list the occupation names of the table, tell the agent to save the table's name when the employee's words clearly name one entry, and to show the list and ask when the words fit several entries or none. The rule for an occupation that is stored but missing from the table stays
    - `car_advisor/agent/tools.py`: the docstring of `ProfileChanges.occupation` says to use the table's name
    - The instructions also tell the agent that Today with no car needs the occupation, and to ask for it when the comparison lists it as missing
    - Tests: `tests/agent/test_advisor.py` checks that all 14 names are in the instructions; `tests/agent/test_scenarios.py` runs an employee with no car whose Today waits for the occupation
    - _Requirements: 18.5, 18.12_

  - [ ] 17.6 Suggest the catalog participation to the representative
    - `car_advisor/api/rep.py` and `car_advisor/api/schemas.py`: a participation question about a catalog car carries `suggested_amount`, the car's `default_participation`. Other questions carry `null`
    - `app/shared/api-models.ts`, `app/rep/answer-values.ts`, `app/rep/answer-form.*` and `app/rep/rep.copy.ts`: show "in the catalog: ₪300" next to the amount field and start the field with that amount. The effective date stays empty and required
    - Tests: `tests/api/test_rep.py` for the amount and the `null`, and the specs of the answer form and the rep question for the hint and the starting value
    - _Requirements: 18.2, 18.13_

  - [x] 17.7 Show the employee's share of the gap in the table
    - `app/shared/api-models.ts`, `app/employee/comparison/comparison.copy.ts`, `comparison-table.*`: the row "חלק העובד בפער", next to "חלק המעסיק בפער", only when a column has a Family_Track car. The testing state in `app/employee/testing/conversation-state.ts` gets the field
    - `comparison-table.spec.ts` checks the row for a family column, and that the row is absent without one
    - _Requirements: 18.14_

  - [x] 17.8 Update the documentation
    - `README.md` and `.env.example`: the data files are the challenge's, and where Strauss's files go
    - `docs/explanations.he.md`:
      - the calculation steps (the contribution base with bonuses, no employer study fund deposit, the family-track car with a participation and a share, Today with no car);
      - the rules table and the "what to replace for the pilot" table (the new fields `default_participation`, `variable_pay_in_contribution_base`, no `family_track.car_cost`);
      - the worked example becomes the sample employee E-001, with the figures of the sheet;
      - the open questions: the three pending items and the assumptions of Req 18, with the resolved ones listed as resolved;
      - a short passage about the participation that the representative sees as a suggestion
    - `tests/test_docs.py`: the rules table, the worked example against `compare()` for E-001, and the open questions
    - _Requirements: 17.4, 17.13, 17.14, 18.17_

  - [x] 17.9 Run the full check again
    - `./check.sh` exits 0, and `TEST_MSSQL_URL=... uv run pytest -m mssql` passes
    - A clean start of the Compose stack in a separate project name loads the new catalog without `CatalogMismatch`, and `/api/health` returns 200
    - _Requirements: 4.6, 15.5, 18.1_

## Notes

- Tasks marked `*` are optional: Requirement 13 (document upload) and the property-based tests. Every other task is required, including all unit tests and the challenge rule examples.
- Each task lists the requirement clauses it implements, and each property test names its design property.
- Requirements 15.6 (English code, Hebrew only for users) and 15.8 (no dead code) apply to every task through the rules in the Overview.
- Checkpoints 6 and 10 and the live checks in task 16 are the points to stop and confirm with the user.
- Only task 16 needs Docker and the user's OpenAI API key; checkpoint 10 needs Docker. Everything else runs offline.
- Every property test writes to `tests/strategies.py`, so the graph below runs them one per wave.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4", "2.1", "11.1"] },
    { "id": 2, "tasks": ["1.5", "2.2", "11.2"] },
    { "id": 3, "tasks": ["2.3", "2.4", "11.3"] },
    { "id": 4, "tasks": ["2.5", "3.1", "4.1", "11.5"] },
    { "id": 5, "tasks": ["2.6", "2.9", "3.2", "4.3", "11.6"] },
    { "id": 6, "tasks": ["2.7", "2.13", "3.3", "4.6", "11.7"] },
    { "id": 7, "tasks": ["2.8", "3.4", "4.7", "11.8"] },
    { "id": 8, "tasks": ["2.10", "3.5", "5.1"] },
    { "id": 9, "tasks": ["2.11", "5.2"] },
    { "id": 10, "tasks": ["2.12", "5.3"] },
    { "id": 11, "tasks": ["3.6", "5.4", "5.5"] },
    { "id": 12, "tasks": ["3.7", "7.1"] },
    { "id": 13, "tasks": ["3.8", "7.2"] },
    { "id": 14, "tasks": ["3.9", "7.3"] },
    { "id": 15, "tasks": ["4.2", "7.4"] },
    { "id": 16, "tasks": ["4.4", "7.5"] },
    { "id": 17, "tasks": ["4.5", "7.6"] },
    { "id": 18, "tasks": ["4.8", "8.1"] },
    { "id": 19, "tasks": ["4.9", "8.2"] },
    { "id": 20, "tasks": ["4.10", "8.3"] },
    { "id": 21, "tasks": ["4.11", "8.4"] },
    { "id": 22, "tasks": ["5.6", "8.5", "9.1"] },
    { "id": 23, "tasks": ["5.7", "8.6", "9.2"] },
    { "id": 24, "tasks": ["5.8", "9.3", "11.4"] },
    { "id": 25, "tasks": ["5.9", "9.4", "12.1"] },
    { "id": 26, "tasks": ["5.10", "9.5", "12.2", "13.1"] },
    { "id": 27, "tasks": ["5.11", "9.6", "12.3", "13.2"] },
    { "id": 28, "tasks": ["5.12", "12.4", "13.3", "14.1"] },
    { "id": 29, "tasks": ["5.13", "12.5"] },
    { "id": 30, "tasks": ["5.14", "12.6"] },
    { "id": 31, "tasks": ["7.7", "12.7"] },
    { "id": 32, "tasks": ["7.8", "12.8", "12.9"] },
    { "id": 33, "tasks": ["7.9", "14.2"] },
    { "id": 34, "tasks": ["7.10", "14.3"] },
    { "id": 35, "tasks": ["7.11", "14.4", "15.1", "15.2"] },
    { "id": 36, "tasks": ["7.12", "15.3"] },
    { "id": 37, "tasks": ["7.13", "15.4"] },
    { "id": 38, "tasks": ["8.7", "15.5"] },
    { "id": 39, "tasks": ["8.8"] },
    { "id": 40, "tasks": ["9.7"] },
    { "id": 41, "tasks": ["9.8"] },
    { "id": 42, "tasks": ["9.9"] },
    { "id": 43, "tasks": ["9.10"] },
    { "id": 44, "tasks": ["9.11"] },
    { "id": 45, "tasks": ["16.1"] },
    { "id": 46, "tasks": ["17.1"] },
    { "id": 47, "tasks": ["17.2"] },
    { "id": 48, "tasks": ["17.3"] },
    { "id": 49, "tasks": ["17.4"] },
    { "id": 50, "tasks": ["17.5"] },
    { "id": 51, "tasks": ["17.6"] },
    { "id": 52, "tasks": ["17.7"] },
    { "id": 53, "tasks": ["17.8"] },
    { "id": 54, "tasks": ["17.9"] },
    { "id": 55, "tasks": ["16.2"] },
    { "id": 56, "tasks": ["16.3"] },
    { "id": 57, "tasks": ["16.4", "16.5"] },
    { "id": 58, "tasks": ["16.6"] }
  ]
}
```
