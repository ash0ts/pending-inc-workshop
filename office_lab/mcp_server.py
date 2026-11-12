"""Real stdio MCP server for the simulated onboarding service."""

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from office_lab.simulator import execute

server = FastMCP("Pending onboarding")


def action(name, arguments):
    path = Path(os.environ["OFFICE_STATE"])
    bundle = json.loads(path.read_text())
    world, output = execute(bundle["world"], name, arguments, bundle.get("fault", ""))
    bundle["world"] = world
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(bundle))
    temporary.replace(path)
    return output


@server.tool()
def onboarding_status(account_id: str) -> dict:
    """Read subscription and current memberships before deciding what to provision."""
    return action("onboarding_status", {"account_id": account_id})


@server.tool()
def provision_member(account_id: str, email: str, role: str, operation_id: str) -> dict:
    """Provision an explicitly requested role. A timeout may follow a committed change."""
    return action(
        "provision_member",
        dict(account_id=account_id, email=email, role=role, operation_id=operation_id),
    )


if __name__ == "__main__":
    server.run(transport="stdio")
