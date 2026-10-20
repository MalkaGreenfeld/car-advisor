from typing import Final

# Every router is created under this prefix, so the route template that the request log shows is
# the whole path that a client calls. It is defined here because the routers cannot import app.py,
# which imports them.
API_PREFIX: Final = "/api"
