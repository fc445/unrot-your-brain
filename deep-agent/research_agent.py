#!/usr/bin/env python3
"""
Minimal Deep Agents research agent — deepagents-python-quickstart, adapted
to route through OpenRouter instead of a direct Anthropic/OpenAI/Google key.

Follows https://docs.langchain.com/oss/python/deepagents/quickstart, but
swaps Tavily / the provider-native web_search tool for OpenRouter's built-in
":online" web search, which is appended to the model slug and returned as
response annotations rather than an explicit tool call. That means this
agent has no internet_search tool in its `tools=[]` list — search happens
automatically on every model call OpenRouter makes for this request.

Usage:
    uv run research_agent.py "What is LangGraph?"
"""

import os
import sys

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from deepagents import create_deep_agent

load_dotenv()

OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free:online"

RESEARCH_INSTRUCTIONS = """You are an expert researcher. Your job is to conduct thorough \
research and then write a polished report.

Web search results are grounded automatically for you on every model call — you do not \
need to call a separate search tool. Cite specific facts you find.
"""


def build_agent():
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Set OPENROUTER_API_KEY in deep-agent/.env before running.", file=sys.stderr)
        sys.exit(1)

    model = ChatOpenAI(
        model=OPENROUTER_MODEL,
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt=RESEARCH_INSTRUCTIONS,
    )


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "What is LangGraph?"

    agent = build_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    print(result["messages"][-1].content)


if __name__ == "__main__":
    main()
