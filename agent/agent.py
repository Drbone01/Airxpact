"""Airxpact agent entrypoint for Amazon Bedrock AgentCore Runtime."""
import ast
import logging
import operator
import os
import re
from datetime import date as Date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.identity.auth import requires_access_token
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("airxpact")

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ["MODEL_ID"]
MEMORY_ID = os.environ.get("MEMORY_ID", "")
KB_ID = os.environ.get("KB_ID", "")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
GATEWAY_OAUTH_PROVIDER = os.environ.get("GATEWAY_OAUTH_PROVIDER", "")
GATEWAY_SCOPE = os.environ.get("GATEWAY_SCOPE", "")
WORKLOAD_NAME = os.environ.get("WORKLOAD_NAME", "")

app = BedrockAgentCoreApp()
kb_client = boto3.client("bedrock-agent-runtime", region_name=REGION)

SYSTEM_PROMPT = """You are Airxpact, a calm, clear and friendly AI travel and aviation expert.
Right now it is {now_lagos} in Lagos (WAT, UTC+1) and {now_utc} UTC. Use these for "today", "tomorrow",
"next Friday" and for checking that dates are in the future.

YOUR CAPABILITIES: route planning, flight offers, live aviation weather (METAR/TAF), destination forecasts,
departure-day scheduling and calendar files, flight status, buses and ground transport, places to visit,
translators and language help, currencies and exchange rates, visas and entry rules (from official
sources), airline policies, and booking requests handled by the Airxpact team.

HOW YOU WORK ON EVERY MESSAGE (silently):
1. Understand the request; it may combine several capabilities.
2. Call every tool you need IN THIS SAME TURN, then answer once.
   - NEVER reply with only "let me search", "one moment" or "I'll check". You cannot send a later
     message by yourself, so the traveller would be left waiting. Either call the tool now or explain
     what you need from them.
3. Check your answer before sending: dates, weekdays, totals, currencies and names must be consistent.

TRUTH AND SOURCES (most important):
- Prices, availability, schedules, weather, exchange rates, flight status, visa and entry rules,
  airline policies and opening hours change. Only state them from a tool result in this turn,
  and name the source (for example "Air Peace baggage page, checked today" or "Duffel live fares").
- If a tool fails or returns nothing, say exactly which information you could not get, give the
  official website to check, and continue with whatever you could verify. Never invent a number.
- Clearly label anything that is not live as an "estimate" and say what it is based on.
- Airline baggage, cancellation, refund and check-in policies: call search_aviation_library first
  (it holds pages crawled from airline and authority websites, with the date they were checked).
  If it has nothing, use web search limited to the airline's official domain. Quote the source URL.
- Visas and entry rules: use web search on official government or embassy sites only, give the link,
  and always tell the traveller to confirm with the embassy before booking.

MONEY, DATES AND BUDGETS:
- Use get_country_info to find a country's currency, languages, time zone and calling code.
  Never guess a currency.
- Use get_exchange_rate for every conversion. Show amounts in the local currency and in the
  traveller's home currency (default Nigerian naira, NGN, unless memory says otherwise), and say
  the rate date.
- Use date_calculator for every list of dates, weekdays, trip lengths and countdowns, and calculator
  for every total. In itineraries, day 1 must be the arrival date, the number of days must match the
  dates, and the budget table must add up and stay within the traveller's budget. If it cannot fit,
  say so and show the cheapest option.

LANGUAGE:
- Reply in the language the traveller writes in (for example English, Nigerian Pidgin, Yoruba, Igbo,
  Hausa, French, Arabic, Portuguese or Spanish). If memory holds a preferred language, use it.
- When the destination's main language differs from the traveller's, proactively offer: a short
  phrase sheet (phrase, translation, simple pronunciation), translation of any message they need,
  and translators or interpreters near them (search places for "certified translator" or
  "interpreter" in that city, and say they should check credentials and reviews).

GREETINGS:
- If the conversation history or your memory shows you have already met this traveller, answer a
  greeting with one short, warm line (use their name if you know it), e.g. "Hi again Ada! How can I
  help today?". Give the full introduction only on the very first contact.

GROUND TRANSPORT AND BOOKINGS:
- For buses, trains and transfers use search_ground_transport; if it has no results for that
  route, use web search for operators that serve it and give their official booking links.
- create_booking_request records a booking request that the Airxpact team confirms (flights,
  buses, airport transfers, translators, hotels). Only call it after the traveller has confirmed every
  detail and given a name and contact phone or email. Give them the booking reference and explain it
  is a request, not a confirmed ticket, until the team confirms. Use get_booking_status when they ask.
- Never ask for or accept card numbers or payment details in chat.

SAFETY: for safety, medical, legal or immigration questions give general guidance and refer to the
official authority. Flag risky connections (under 60 min domestic, 90 min international), storms,
fog, strong winds and Harmattan haze.

STYLE: plain, simple language; the answer first, then details; explain WHY you recommend an option;
end with one clear next step.
"""


@tool
def search_aviation_library(query: str) -> str:
    """Search the curated Airxpact aviation library: airports, IATA/ICAO codes, terminals,
    airline policies (baggage, check-in, changes, refunds, delays), aircraft and operations,
    fare classes, travel documents, passenger rights, and how to read METAR/TAF.

    Args:
        query: the question or keywords to look up.
    """
    if not KB_ID:
        return "The aviation library is not configured."
    try:
        resp = kb_client.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 5}},
        )
    except Exception as exc:
        log.exception("Knowledge base retrieve failed")
        return f"Library lookup failed: {exc}"
    chunks = []
    for i, r in enumerate(resp.get("retrievalResults", []), start=1):
        text = r.get("content", {}).get("text", "")
        loc = r.get("location", {})
        src = (loc.get("s3Location") or {}).get("uri") or (loc.get("webLocation") or {}).get("url") or "unknown"
        chunks.append(f"[{i}] source: {src.split('/')[-1]}\n{text}")
    return "\n\n".join(chunks) if chunks else "No matching documents in the aviation library."


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg,
        ast.UAdd: operator.pos}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("Only numbers and + - * / % ** ( ) are allowed")


@tool
def calculator(expression: str) -> str:
    """Exact arithmetic for budgets, totals, per-person costs and conversions.
    Use it for EVERY total in an itinerary or budget.

    Args:
        expression: e.g. "864 + 3*120 + 4*85" or "250000 / 1530.25"
    """
    try:
        value = _eval(ast.parse(expression.replace(",", ""), mode="eval"))
        return f"{expression} = {round(value, 4):,}"
    except Exception as exc:
        return f"Could not calculate '{expression}': {exc}"


@tool
def date_calculator(operation: str, date: str = "", days: int = 0, end_date: str = "",
                    timezone: str = "Africa/Lagos") -> str:
    """Exact dates. Use it for every itinerary date list, weekday, trip length or countdown.

    Args:
        operation: "now" (current date/time in timezone), "weekday" (weekday of date),
            "add_days" (date + days, can be negative), "days_between" (date to end_date),
            "list" (each date from date for `days` days, with weekdays)
        date: start date YYYY-MM-DD
        days: number of days for add_days or list
        end_date: end date YYYY-MM-DD for days_between
        timezone: IANA time zone, e.g. Europe/London
    """
    try:
        if operation == "now":
            now = datetime.now(ZoneInfo(timezone))
            return f"Now in {timezone}: {now.strftime('%A %d %B %Y, %H:%M')}"
        start = Date.fromisoformat(date)
        if operation == "weekday":
            return f"{start.isoformat()} is a {start.strftime('%A')}"
        if operation == "add_days":
            d = start + timedelta(days=days)
            return f"{start.isoformat()} + {days} days = {d.isoformat()} ({d.strftime('%A')})"
        if operation == "days_between":
            end = Date.fromisoformat(end_date)
            n = (end - start).days
            return f"{start.isoformat()} to {end.isoformat()} = {n} days ({n} nights, {n + 1} calendar days)"
        if operation == "list":
            return "\n".join(f"Day {i + 1}: {(start + timedelta(days=i)).strftime('%A %d %B %Y')}"
                             for i in range(max(1, min(days, 60))))
        return "Unknown operation. Use now, weekday, add_days, days_between or list."
    except Exception as exc:
        return f"Date error: {exc}"


_token_fetcher = None


def _get_token_fetcher():
    global _token_fetcher
    if _token_fetcher is None:
        @requires_access_token(
            provider_name=GATEWAY_OAUTH_PROVIDER,
            scopes=[GATEWAY_SCOPE] if GATEWAY_SCOPE else [],
            auth_flow="M2M",
            into="access_token",
        )
        async def _fetch(*, access_token: str) -> str:
            return access_token
        _token_fetcher = _fetch
    return _token_fetcher


def _ensure_workload_token(actor_id: str) -> None:
    """With JWT inbound auth the Runtime supplies a workload access token automatically.
    With IAM inbound auth (console Test button) it does not, so fetch one ourselves."""
    if BedrockAgentCoreContext.get_workload_access_token():
        return
    if not WORKLOAD_NAME:
        raise ValueError("No workload access token (IAM inbound auth) and WORKLOAD_NAME is not set")
    dp = boto3.client("bedrock-agentcore", region_name=REGION)
    token = dp.get_workload_access_token_for_user_id(workloadName=WORKLOAD_NAME, userId=actor_id)[
        "workloadAccessToken"
    ]
    BedrockAgentCoreContext.set_workload_access_token(token)


async def get_gateway_token(actor_id: str) -> str:
    _ensure_workload_token(actor_id)
    return await _get_token_fetcher()()


def clean_id(value: str, fallback: str) -> str:
    """AgentCore Memory IDs must start with a letter/number and use only letters, numbers, - _ /"""
    value = re.sub(r"[^a-zA-Z0-9\-_/]", "-", str(value or ""))[:100].strip("-/")
    return value if value and value[0].isalnum() else fallback


def build_session_manager(actor_id: str, session_id: str):
    if not MEMORY_ID:
        return None
    config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID,
        session_id=session_id,
        actor_id=actor_id,
        retrieval_config={
            f"/users/{actor_id}/preferences": RetrievalConfig(top_k=5, relevance_score=0.3),
            f"/users/{actor_id}/facts": RetrievalConfig(top_k=8, relevance_score=0.3),
            f"/users/{actor_id}/summaries/{session_id}": RetrievalConfig(top_k=3, relevance_score=0.3),
        },
    )
    return AgentCoreMemorySessionManager(agentcore_memory_config=config, region_name=REGION)


@app.entrypoint
async def invoke(payload, context):
    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        return {"error": "Send a JSON body like {\"prompt\": \"...\"}"}

    actor_id = clean_id(payload.get("actor_id"), "anonymous")
    session_id = clean_id(
        payload.get("session_id") or getattr(context, "session_id", None), "default-session"
    )

    model = BedrockModel(model_id=MODEL_ID, region_name=REGION, temperature=0.3)
    session_manager = build_session_manager(actor_id, session_id)
    system_prompt = SYSTEM_PROMPT.format(
        now_lagos=datetime.now(ZoneInfo("Africa/Lagos")).strftime("%A %d %B %Y, %H:%M"),
        now_utc=datetime.now(timezone.utc).strftime("%A %d %B %Y, %H:%M"),
    )

    local_tools = [search_aviation_library, calculator, date_calculator]
    mcp_client = None
    gateway_status = "not configured"
    if GATEWAY_URL and GATEWAY_OAUTH_PROVIDER:
        try:
            token = await get_gateway_token(actor_id)
            mcp_client = MCPClient(url=GATEWAY_URL, headers={"Authorization": f"Bearer {token}"})
            mcp_client.start()
            gateway_status = "connected"
        except Exception as exc:
            log.exception("Could not connect to the AgentCore Gateway; continuing with local tools only")
            gateway_status = f"ERROR {type(exc).__name__}: {str(exc)[:300]}"
            mcp_client = None

    tool_names = []
    try:
        gateway_tools = mcp_client.list_tools_sync() if mcp_client else []
        tool_names = [getattr(t, "tool_name", str(t)) for t in local_tools + gateway_tools]
        log.info("Gateway %s; %d tools available: %s", gateway_status, len(tool_names), ", ".join(tool_names))
        agent = Agent(
            model=model,
            system_prompt=system_prompt,
            tools=local_tools + gateway_tools,
            session_manager=session_manager,
            trace_attributes={"actor.id": actor_id, "session.id": session_id},
        )
        result = await agent.invoke_async(prompt)
        text = str(result)
    finally:
        if mcp_client:
            try:
                mcp_client.stop(None, None, None)
            except Exception:
                pass

    return {"response": text, "session_id": session_id, "actor_id": actor_id,
            "gateway_status": gateway_status, "tools_available": tool_names}


if __name__ == "__main__":
    app.run()
