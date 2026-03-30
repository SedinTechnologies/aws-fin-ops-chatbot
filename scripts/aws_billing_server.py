from contextlib import asynccontextmanager
from awslabs.billing_cost_management_mcp_server.server import mcp, setup

def create_app():
    app = mcp.http_app()

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        await setup()                          # register all tools
        async with original_lifespan(app):     # start session manager task group
            yield

    app.router.lifespan_context = lifespan
    return app
