"""Fixed Sandbox entry point. No provider credentials or grading code."""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

from office_lab.simulator import check_work, execute


async def mcp_action(path, name, arguments, *, clean_environment=False):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "office_lab.mcp_server"],
        env={
            **(
                {"PATH": os.defpath, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
                if clean_environment
                else os.environ
            ),
            "OFFICE_STATE": str(path.resolve()),
        },
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            discovered = await session.list_tools()
            schemas = [tool.model_dump(mode="json") for tool in discovered.tools]
            protocol = {
                "transport": "stdio",
                "protocol_version": initialized.protocolVersion,
                "methods": ["initialize", "tools/list"],
                "tools": schemas,
            }
            if name == "discover_onboarding":
                return {"ok": True, "tools": schemas, "mcp": protocol}
            result = await session.call_tool(name, arguments)
            protocol["methods"].append("tools/call")
            output = getattr(result, "structuredContent", None)
            if output is None:
                output = json.loads(
                    next(item.text for item in result.content if item.type == "text")
                )
            return {**output, "mcp": protocol}


def run_file(input_path):
    source = Path(input_path)
    target = source.with_suffix(".result.json")
    if target.exists():
        return json.loads(target.read_text())
    request = json.loads(source.read_text())
    state_path = source.with_suffix(".state.json")
    state_path.write_text(
        json.dumps({"world": request["world"], "fault": request.get("fault", "")})
    )
    name, arguments = request["name"], request["arguments"]
    world = request["world"]
    if name in {"onboarding_status", "provision_member", "discover_onboarding"}:
        output = asyncio.run(mcp_action(state_path, name, arguments))
        world = json.loads(state_path.read_text())["world"]
    elif name == "check_work":
        output = {"ok": True, **check_work(world, request["objective"])}
    elif name == "lookup_invoice":
        from office_lab.simulator import lookup_invoice

        try:
            output = lookup_invoice(world, **arguments)
        except Exception as exc:
            output = {"ok": False, "error": str(exc)}
    else:
        world, output = execute(world, name, arguments, request.get("fault", ""))
    files = {
        p: hashlib.sha256(Path("harness", p).read_bytes()).hexdigest()
        for p in request["harness_files"]
    }
    result = {
        "world": world,
        "output": output,
        "files_sha256": files,
        "tool_source_sha256": hashlib.sha256(
            Path("office_lab/simulator.py").read_bytes()
        ).hexdigest(),
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(result))
    temporary.replace(target)
    return result


if __name__ == "__main__":
    print(json.dumps(run_file(sys.argv[1])))
