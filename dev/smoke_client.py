import asyncio
import os

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


async def main():
    token = os.environ.get("GUARD_TOKEN")
    transport = StreamableHttpTransport(
        "http://127.0.0.1:8080/mcp",
        headers={"Authorization": f"Bearer {token}"} if token else None,
    )
    async with Client(transport) as c:
        for t in await c.list_tools():
            print(t.name)
        r = await c.call_tool(
            "email_send_email",
            {"to": "dana@yossibakery.com", "subject": "hi", "body": "test"},
        )
        print(r.content[0].text)


asyncio.run(main())
