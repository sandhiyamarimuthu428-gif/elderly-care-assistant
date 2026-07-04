import datetime
import json
import re
from typing import AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.workflow import Workflow, Edge, FunctionNode
from google.adk.tools import AgentTool
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
from google.genai import types

from app.config import config

# ---------------------------------------------------------------------------
# MCP Toolset — connects to our custom mcp_server.py via stdio
# ---------------------------------------------------------------------------
mcp_tools = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="uv",
            args=["run", "python", "-m", "app.mcp_server"],
        ),
    ),
)

# ---------------------------------------------------------------------------
# Sub-Agent 1: Wellness Companion
# ---------------------------------------------------------------------------
wellness_companion_agent = LlmAgent(
    name="wellness_companion_agent",
    model=config.model,
    instruction=(
        "You are a friendly wellness companion for elderly individuals. Your job is to check on their physical "
        "and emotional well-being, provide supportive and empathetic conversation, and answer general health-related "
        "questions. Always maintain a warm, respectful, patient, and easy-to-understand tone. "
        "Use the available MCP tools to read the daily schedule or log wellness info if requested."
    ),
    description="Talks to the elderly user, checks on their wellness, and provides empathetic companion support.",
    tools=[mcp_tools],
)

# ---------------------------------------------------------------------------
# Sub-Agent 2: Care Scheduler
# ---------------------------------------------------------------------------
care_scheduler_agent = LlmAgent(
    name="care_scheduler_agent",
    model=config.model,
    instruction=(
        "You are a scheduling and medication assistant for elderly individuals. Your job is to manage medication logs, "
        "doctor appointments, and daily activities. Use the available MCP tools to get current schedules, add appointments, "
        "or record when medication is taken. "
        "If the user asks to schedule a new appointment or modify/log a medication, you must draft the details and state "
        "that caregiver approval is required. You MUST include the phrase '[APPROVAL_REQUIRED]' in your response when scheduling "
        "or logging medications."
    ),
    description="Manages medication logs, daily schedules, and doctor appointments.",
    tools=[mcp_tools],
)

# ---------------------------------------------------------------------------
# Central Orchestrator Agent (delegates to sub-agents via AgentTool)
# ---------------------------------------------------------------------------
orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    model=config.model,
    instruction=(
        "You are the central orchestrator for the Elderly Care Assistant. Your task is to analyze the user's input "
        "and delegate to the appropriate specialized agent using your tools. "
        "Use wellness_companion_agent for general wellness chats, emotional support, and general questions. "
        "Use care_scheduler_agent for scheduling appointments, logging medication, or viewing schedules. "
        "Call the appropriate agent tool, retrieve its response, and present it clearly to the user. "
        "If the specialized agent indicates that caregiver approval is required, or if the request involves scheduling "
        "or medications, you MUST include the phrase '[APPROVAL_REQUIRED]' in your final output."
    ),
    description="Analyzes user requests and delegates to wellness or care scheduling sub-agents.",
    tools=[AgentTool(wellness_companion_agent), AgentTool(care_scheduler_agent)],
)

# ---------------------------------------------------------------------------
# 1. Security Checkpoint Node
# ---------------------------------------------------------------------------
def _security_checkpoint(ctx: Context, node_input: types.Content):
    """Inspects user input for PII, prompt injection, and medical emergencies."""
    # Extract prompt text from Content
    prompt_text = ""
    if node_input and node_input.parts:
        prompt_text = "".join(part.text for part in node_input.parts if part.text)

    # PII Scrubbing (SSN, Phone, Email)
    clean_text = prompt_text
    ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b"
    phone_pattern = r"\b\d{3}-\d{3}-\d{4}\b"
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"

    clean_text = re.sub(ssn_pattern, "[REDACTED_SSN]", clean_text)
    clean_text = re.sub(phone_pattern, "[REDACTED_PHONE]", clean_text)
    clean_text = re.sub(email_pattern, "[REDACTED_EMAIL]", clean_text)

    pii_scrubbed = clean_text != prompt_text

    # Prompt Injection keywords
    injection_keywords = [
        "ignore previous instructions", "system prompt",
        "override instructions", "jailbreak", "bypass security",
    ]
    has_injection = any(kw in clean_text.lower() for kw in injection_keywords)

    # Medical Emergency Detection
    emergency_keywords = [
        "emergency", "heart attack", "chest pain",
        "breathing difficulty", "stroke", "call 911",
    ]
    is_emergency = any(kw in clean_text.lower() for kw in emergency_keywords)

    # Audit Log
    log_entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": ctx.session.id,
        "pii_scrubbed": pii_scrubbed,
        "injection_detected": has_injection,
        "emergency_detected": is_emergency,
        "severity": "INFO",
        "action": "pass",
    }

    if has_injection:
        log_entry["severity"] = "CRITICAL"
        log_entry["action"] = "block_injection"
        print(json.dumps(log_entry))
        return Event(
            output="Security Alert: Potential prompt injection detected. Request blocked.",
            route="security_alert",
        )

    if is_emergency:
        log_entry["severity"] = "WARNING"
        log_entry["action"] = "block_emergency"
        print(json.dumps(log_entry))
        return Event(
            output="Emergency Alert: If you are experiencing a medical emergency, please call 911 or your local emergency services immediately.",
            route="security_alert",
        )

    if pii_scrubbed:
        log_entry["severity"] = "WARNING"
        log_entry["action"] = "scrub_pii"

    print(json.dumps(log_entry))

    # Return the (possibly scrubbed) text as a Content object for the orchestrator
    clean_content = types.Content(
        role="user", parts=[types.Part.from_text(text=clean_text)]
    )
    return Event(output=clean_content, route="clean")


# Wrap as FunctionNode so it can be used in Edge objects with route=
security_checkpoint = FunctionNode(func=_security_checkpoint)


# ---------------------------------------------------------------------------
# 2. Security Alert Handler (pass-through for blocked requests)
# ---------------------------------------------------------------------------
def _security_alert_handler(node_input: str) -> str:
    """Pass-through: returns the security alert message."""
    return node_input


security_alert_handler = FunctionNode(func=_security_alert_handler)


# ---------------------------------------------------------------------------
# 3. Human-in-the-Loop Approval Node
# ---------------------------------------------------------------------------
def _human_approval(ctx: Context, node_input):
    """Checks orchestrator output for [APPROVAL_REQUIRED] and pauses for caregiver consent."""
    # Extract text from Content or str
    text = ""
    if isinstance(node_input, str):
        text = node_input
    elif isinstance(node_input, types.Content) and node_input.parts:
        text = "".join(part.text for part in node_input.parts if part.text)
    elif node_input is not None:
        text = str(node_input)

    if "[APPROVAL_REQUIRED]" in text:
        if not ctx.resume_inputs or "caregiver_approval" not in ctx.resume_inputs:
            yield RequestInput(
                interrupt_id="caregiver_approval",
                message="[Caregiver Approval Required] The agent wants to modify schedules or medications. Do you approve? (yes/no)",
            )
            return

        approval_response = ctx.resume_inputs["caregiver_approval"]
        if "yes" in approval_response.lower():
            cleaned_text = text.replace("[APPROVAL_REQUIRED]", "").strip()
            yield Event(
                output=f"Caregiver approved: {cleaned_text}",
                state={"caregiver_approved": True},
            )
        else:
            yield Event(
                output="Caregiver denied: This action was not approved.",
                state={"caregiver_approved": False},
            )
    else:
        yield Event(output=text)


# ---------------------------------------------------------------------------
# 4. Final Output Formatter (emits content for web UI display)
# ---------------------------------------------------------------------------
def _final_output(node_input):
    """Renders the final result in the web UI and forwards it as output."""
    yield Event(
        content=types.Content(
            role="model", parts=[types.Part.from_text(text=str(node_input))]
        )
    )
    yield Event(output=str(node_input))


# ---------------------------------------------------------------------------
# Workflow Graph
# ---------------------------------------------------------------------------
root_agent = Workflow(
    name="elderly_care_assistant_workflow",
    edges=[
        # Entry → security check
        ("START", security_checkpoint),
        # Conditional routing from security checkpoint
        Edge(from_node=security_checkpoint, to_node=orchestrator_agent, route="clean"),
        Edge(from_node=security_checkpoint, to_node=security_alert_handler, route="security_alert"),
        # Orchestrator → approval gate → final output
        (orchestrator_agent, _human_approval),
        (_human_approval, _final_output),
        # Security alerts → final output directly
        (security_alert_handler, _final_output),
    ],
    description="A workflow for assisting elderly users with wellness and care scheduling, featuring security checks and caregiver approvals.",
)

app = App(
    name="app",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
