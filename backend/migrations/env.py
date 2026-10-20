from alembic import context
from sqlalchemy import Connection

# The app passes its own connection (car_advisor.db.run_migrations), so the migrations run
# inside the caller's transaction and never read a URL that holds the password.
connection: Connection = context.config.attributes["connection"]
context.configure(connection=connection)

with context.begin_transaction():
    context.run_migrations()
