"""
Example: how an agent (or any MCP client) calls PrecisionCalc over stdio.

Run:
    python examples/agent_example.py

It spawns the server as a subprocess, performs the MCP handshake, then calls
the main tools and prints the structured results an LLM agent would receive.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def structured(result) -> dict:
    """Return the tool's structured JSON regardless of SDK attribute naming."""
    return (
        getattr(result, "structured_content", None)
        or getattr(result, "structuredContent", None)
        or {"raw": [c.text for c in result.content if hasattr(c, "text")]}
    )


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["server.py"], cwd=ROOT)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("== Available tools ==")
            for tool in (await session.list_tools()).tools:
                print(f"  - {tool.name}")

            calls = [
                ("health_check", {}),
                ("list_metrics", {}),
                ("calculate_metric",
                 {"metric": "ltv", "params": {"arpu": 120, "churn_rate": 0.04, "gross_margin": 0.82}}),
                ("calculate_metric",
                 {"metric": "ltv_cac_ratio", "params": {"ltv": 2460, "cac": 400}}),
                ("currency_convert",
                 {"amount": 5000, "from_currency": "EUR", "to_currency": "GBP"}),
                ("business_days",
                 {"operation": "count_business_days", "start_date": "2024-12-20",
                  "end_date": "2024-12-31", "region": "US"}),
                ("compound_growth",
                 {"operation": "cagr", "begin_value": 100000, "end_value": 250000, "years": 5}),
            ]

            for name, args in calls:
                result = await session.call_tool(name, args)
                print(f"\n== {name}({json.dumps(args)}) ==")
                print(json.dumps(structured(result), indent=2)[:900])


if __name__ == "__main__":
    asyncio.run(main())
