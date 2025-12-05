"""CLI entrypoint for chatting with the MCP-backed agent."""

from __future__ import annotations

import argparse
import asyncio

from agent import ChatAgent


async def run_chat(*, session_id: str) -> None:
    agent = ChatAgent()

    print("Type your message (Ctrl+C to exit):")
    while True:
        try:
            text = input("> ").strip()
        except KeyboardInterrupt:
            print("\nbye!")
            return

        if not text:
            continue

        response = await agent.respond(session_id=session_id, user_message=text)
        print(response["text"] or "[no text response]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal chat CLI for the MCP agent.")
    parser.add_argument("--session", default="cli", help="Session ID to reuse between runs.")
    args = parser.parse_args()

    asyncio.run(run_chat(session_id=args.session))


if __name__ == "__main__":
    main()

