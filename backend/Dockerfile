FROM python:3.12-slim

# mssql-python bundles the ODBC driver, which needs these three shared libraries.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libltdl7 libkrb5-3 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

# Bytecode is compiled at build time because the app user cannot write .pyc files later.
# uv installs into the image's own Python 3.12 and keeps its cache out of the layers.
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/backend/.venv/bin:$PATH"

# compose bind-mounts config/ and data/ at /app/config and /app/data. The relative paths in
# the settings (../config, ../data) resolve there from this directory.
WORKDIR /app/backend

# Dependencies first, so a source edit does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# car_advisor.db looks for alembic.ini two levels above the package. That holds only for the
# editable install that uv sync makes by default, with alembic.ini next to src/.
COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev

USER app
EXPOSE 8000

# The app's own middleware writes the request log, so uvicorn's access log stays off.
CMD ["uvicorn", "car_advisor.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
