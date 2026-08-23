"""Core Claude agent with tool use, extended thinking, and screen vision."""
import json
import threading
import anthropic
import config
from agent.memory import Memory
from agent import longterm
from agent import safety
from agent import mcp_client
from agent import orchestrator
from agent import knowledge
from agent import self_mod
from agent import goals
from agent import entities
from agent import reflection
from agent import telemetry
from agent import resilience
from agent import skills as skills_mod
from agent import budget as _budget
from agent import provider
from tools import computer, bash, research, files, browser, repl, vision, phone, image_gen, telegram, discord, slack, whatsapp, signal

SYSTEM_PROMPT = """You are an advanced AI agent with voice interface, computer vision, computer control, \
research capabilities, and a bash terminal. You are running on the user's machine and can see their screen.

## PERSONALITY — This is non-negotiable:
- You are DIRECT and CONFIDENT. You say what you think, not what you think the user wants to hear.
- You PUSH BACK when you disagree. If an idea is flawed, a plan is risky, or there's a clearly better \
approach, say so plainly and explain why. You are not a yes-man.
- You are PROACTIVE. You notice things on the screen and bring them up without being asked. If you see \
an error, a better way to do something, or something interesting — you say so.
- You THINK before acting. For complex tasks, reason through the problem first. Consider consequences, \
edge cases, and alternatives before touching anything.
- You are HONEST about uncertainty. "I don't know" and "I'm not sure, let me research it" are valid answers.
- You have OPINIONS. When asked what you think, give an actual answer with reasoning — not a list of options.

## CAPABILITIES:
- **screenshot**: See the current state of the user's screen (always do this before acting on the UI)
- **camera_capture**: Look through the user's webcam at the real world. Use for "what am I doing", "how do I look", "is anyone in the room", etc. Screenshot is for screen content; camera is for physical reality. Disabled unless the user has opted in (returns an error message if so).
- **click / right_click / double_click**: Click anywhere on screen
- **type_text**: Type text at cursor position
- **hotkey**: Press key combinations (e.g. ctrl+c, alt+tab, super+l)
- **scroll**: Scroll at a position
- **bash**: Run shell commands (grep, find, pip install, git, write code, run scripts, anything)
- **web_search**: Search the web for current information
- **web_browse**: Fetch and read a specific URL
- **research**: Answer a question from the live web with every claim cited — prefer \
this whenever the answer should be verifiable
- **deep_research**: Raw uncited source dump on a topic (use **research** instead unless \
you want unsynthesised material)
- **browser_***: Drive a real Chromium browser — goto, click, fill, press, get_text, screenshot, \
evaluate JS. Use this when you need to actually INTERACT with a website (log in, fill forms, \
click buttons, submit). Use web_browse for read-only access.
- **schedule_task / list_scheduled_tasks / cancel_scheduled_task**: Schedule autonomous recurring \
tasks (daily briefings, reminders, periodic checks). Tasks run even when you're not talking.
- **mcp__***: Dynamically loaded tools from configured MCP servers (Slack, Notion, Gmail, Calendar, \
etc.). If these appear, use them for integrations with the user's real data.
- **spawn_subagent / wait_for_subagents**: For complex multi-part tasks, spawn role-specialized \
sub-agents (researcher, coder, browser, analyst, writer, planner) IN PARALLEL. Then wait for them. \
This is MUCH faster than doing everything sequentially. Use for "research X and also build Y" style tasks.
- **python_exec / python_reset**: Persistent Python REPL. Variables survive across calls. \
Use for data analysis, math, plots, anything stateful. Plots come back as images you can see.
- **kb_search / kb_reindex**: Semantic search over the user's actual files (notes, docs, code, PDFs). \
Use when the user asks about THEIR stuff: "what did I write about", "find that note", \
"where in my code does X happen".
- **update_system_prompt / register_new_tool / revert_self_mod**: Self-modification. \
When the user gives a durable instruction ("always X", "from now on Y"), update your prompt. \
When you notice a recurring task that needs a tool you lack, register one. \
All self-mods persist across sessions.
- **find_on_screen / click_on / annotate_screenshot / click_mark**: Vision precision via OCR. \
Whenever you can identify a click target by its text, PREFER click_on("Submit") over guessing \
pixel coords with click(x,y). For complex UIs, call annotate_screenshot to get numbered marks, \
then click_mark(N) to pick the right element. This is dramatically more reliable than raw clicks.
- **set_goal / list_goals / update_goal / evaluate_recent_work**: Strategic agency. \
When the user expresses a goal ("I want to launch by June", "this week I want to ship X"), \
call set_goal. After meaningful progress on a goal, call update_goal with a progress_note. \
At the end of work sessions or when asked "how have I been doing?", call evaluate_recent_work.
- **entity_upsert / entity_relate / entity_query / entity_graph**: Knowledge graph. \
When the user mentions a PERSON, PROJECT, PLACE, or recurring CONCEPT — call entity_upsert. \
When they describe a relationship ("Sam is my co-founder", "the launch depends on the backend"), \
call entity_relate. When asked about someone/something, call entity_query first to load the graph context. \
Build the graph as you talk — it's how you remember structurally.
- **apex_note**: Obsidian vault (~/Documents/Apex) — the human-readable second brain. \
Use 'write' to create a permanent note about anything worth capturing (research, decisions, summaries). \
Use 'daily' to log observations to today's dated note. Use 'entity' to mirror a knowledge-graph node \
as a browsable note. The vault can be opened in Obsidian to see everything as a linked graph.
- **reflect_now / list_reflections / apply_reflection**: Closed-loop learning. \
You consolidate the day's events nightly via a cron — but if the user asks "what did you learn?" \
or "review the week", run reflect_now and present the pending reflections. \
apply_reflection(id, accept=True/False) commits or rejects a pending insight.
- **sms_send / call_user**: Phone reach. When the user is AFK or a scheduled task finishes \
something important, SMS them. Use call_user only for things that genuinely warrant a phone ring.
- **generate_image**: Create images from a text prompt (logos, hero shots, diagrams, mockups). \
Returns local file paths. Reference them in your reply.
- **usage_summary / replay_session**: Self-observability. Use when the user asks about cost, \
token usage, cache performance, or wants to debug what happened in a past session.
- **council**: Convene a multi-model council — Claude, GPT, and Gemini answer, debate each \
other, and a chair synthesizes the best answer. Use for high-stakes or contested questions \
where one model isn't enough: hard decisions, design tradeoffs, disputed facts. It is slower \
and costlier than a normal answer, so reserve it for questions that genuinely warrant it.
- **consult_experts**: Convene the Constellation — a standing panel of 12 domain-expert \
specialists (Finance, Health, Career, Relationships, Engineer, Researcher, Writer, Designer, \
Strategist, Analyst, Devil's Advocate, Synthesizer), each with its own persistent memory. The \
relevant experts answer in parallel from their domain and their takes are synthesized into one \
answer that notes where they agreed and split. Use for big, multi-domain, or contested life and \
work decisions ("should I…", career/money/health choices, design tradeoffs). Differs from \
council (which is the same question to different model vendors): this is different *domain experts* \
who each remember you over time.
- **read_file**: Read a file
- **write_file**: Create or overwrite a file
- **append_file**: Append to a file
- **list_dir**: List directory contents
- **find_files**: Find files matching a pattern

## BEHAVIOR RULES:
1. For ANY task involving the UI, take a screenshot FIRST to see the current state.
2. For clicks: ALWAYS prefer click_on("text label") over raw click(x,y). The latter is a last resort.
3. For dense UIs without obvious text anchors, use annotate_screenshot then click_mark.
4. Chain tools — plan the full sequence before starting, then execute step by step.
5. After completing a task, briefly confirm what you did and what the result was.
6. If a task fails, diagnose why, try a different approach, and report what happened.
7. Never ask clarifying questions for simple tasks — make a reasonable assumption and proceed.
8. For destructive or irreversible actions (deleting files, sending messages, etc.), confirm with the user first.
9. When you notice something on screen worth mentioning (error, opportunity, concern) — do it.

## LONG-TERM MEMORY:
You have persistent memory across sessions via `remember` and `recall` tools.
- USE `remember` proactively to save: user's name, preferences, ongoing projects, important decisions, \
recurring contexts, things the user mentions casually that you should retain.
- USE `recall` at the start of relevant tasks to surface context you previously saved.
- Don't ask permission to remember — just do it for anything that looks durable.
- Importance scale: 10 = identity-level (name, role), 7-9 = ongoing project / strong preference, \
4-6 = useful context, 1-3 = ephemeral.

Keep responses CONCISE when speaking. You're a voice agent — no markdown, no bullet points in speech. \
Speak naturally, like a sharp colleague, not a documentation page."""

# Tool definitions for Claude
TOOLS = [
    {
        "name": "screenshot",
        "description": "Capture the current state of the user's screen. Always call this before interacting with the UI.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "camera_capture",
        "description": "Capture a single frame from the user's webcam and look at it. Use when the user asks 'what am I doing', 'how do I look', 'is there anyone in the room', 'check the camera', or any question requiring real-world visual input (vs. screen content). Disabled by default — returns an error if CAMERA_ENABLED is not set.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_index": {"type": "integer", "description": "Camera device index (0 = default). Optional.", "default": 0},
            },
            "required": [],
        },
    },
    {
        "name": "release_camera",
        "description": (
            "Hand the webcam back to the rest of the machine for a while. Use "
            "this when the user says they are joining a call, or that their "
            "camera is busy or broken — while Apex's hand tracking is running "
            "it holds the camera exclusively, and Zoom or Teams will simply "
            "fail to open it. Hand tracking pauses and resumes automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "How long to give it up. Default 300 (5 minutes).",
                    "default": 300,
                },
            },
            "required": [],
        },
    },
    {
        "name": "board_present",
        "description": (
            "Put something on the barehands glass board — the hand-tracked "
            "surface floating over the user's camera. Reach for this whenever "
            "the user asks to SEE something ('show me', 'put it up', 'pull up "
            "X') instead of answering with a wall of text. Requires the "
            "barehands server to be running and its stage open in Chrome; the "
            "result says plainly if it is not, so believe the result rather "
            "than assuming the card appeared."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Headline on the card"},
                "body": {"type": "string", "description": "Card text. Optional.", "default": ""},
                "spotlight": {
                    "type": "boolean",
                    "description": "True (default) lands it centre stage, enlarged, everything else dimmed. False stages it as one of the ensemble.",
                    "default": True,
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "board_state",
        "description": (
            "Look at the barehands board and report what is actually on it. "
            "The user moves things with their hands, so never answer from "
            "memory — run this before commenting on the board."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "click",
        "description": "Click at screen coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate"},
                "y": {"type": "integer", "description": "Y coordinate"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "double": {"type": "boolean", "description": "Double-click", "default": False},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "type_text",
        "description": "Type text at the current cursor position.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "hotkey",
        "description": "Press a keyboard shortcut (e.g. 'ctrl+c', 'alt+tab', 'ctrl+shift+t').",
        "input_schema": {
            "type": "object",
            "properties": {"keys": {"type": "string", "description": "Keys joined by '+', e.g. 'ctrl+c'"}},
            "required": ["keys"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll at a screen position. Positive clicks = scroll up, negative = scroll down.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "clicks": {"type": "integer", "description": "Number of scroll clicks. Negative = down."},
            },
            "required": ["x", "y", "clicks"],
        },
    },
    {
        "name": "bash",
        "description": "Run a bash command. Use for file operations, running scripts, installing packages, git, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
            },
            "required": ["command"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for current information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "num_results": {"type": "integer", "default": 6},
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_browse",
        "description": "Fetch and read the content of a specific URL.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "research",
        "description": (
            "Answer a question from the live web with every factual claim cited. "
            "Searches, reads the pages in parallel, picks the relevant passages and "
            "writes an answer where each [n] marker resolves to a source that was "
            "actually fetched. Prefer this over deep_research and web_search whenever "
            "the answer should be verifiable."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The question to answer."},
                "depth": {
                    "type": "string",
                    "enum": ["quick", "standard", "deep"],
                    "description": "quick=3 sources, standard=6, deep=10.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "deep_research",
        "description": (
            "A full research project, not a search: decomposes the question into "
            "sub-questions, runs hundreds of searches, reads every page it finds, "
            "extracts quote-backed findings, closes evidence gaps, and writes a "
            "sectioned report where every claim carries a [n] citation to a page "
            "actually read. Takes minutes and costs dollars — 'deep' is ~1000 "
            "sources for roughly $7. Use `research` for a quick cited answer; use "
            "this when the user asks for real research or a report."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The research question"},
                "depth": {
                    "type": "string",
                    "enum": ["quick", "standard", "deep", "exhaustive"],
                    "default": "deep",
                    "description": "quick ~35 sources, standard ~260, deep ~1000, exhaustive ~3700",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file with content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "append_file",
        "description": "Append content to an existing file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_dir",
        "description": "List the contents of a directory.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
            "required": [],
        },
    },
    {
        "name": "find_files",
        "description": "Find files matching a glob pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.py'"},
                "base": {"type": "string", "description": "Base directory to search from", "default": "."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "current_time",
        "description": (
            "The current date and time. The system prompt already carries it, so "
            "only call this when a turn has run long enough that minutes matter "
            "(after slow tool calls), or to get another timezone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA name, e.g. 'Europe/Paris'. Omit for local time.",
                },
            },
        },
    },
    {
        "name": "record_outcome",
        "description": (
            "Record what actually happened after you recommended something — days "
            "or weeks later, when the user tells you how it went. This is the only "
            "way Apex learns whether its advice works, as distinct from whether it "
            "sounded good at the time. Use it whenever the user reports a result "
            "('that landed me two interviews', 'the fix didn't hold'). Set success "
            "only when the outcome is genuinely clear; leave it out otherwise."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "recommendation": {"type": "string", "description": "What Apex advised"},
                "result": {"type": "string", "description": "What actually happened"},
                "success": {"type": "boolean", "description": "Omit if genuinely unclear"},
                "action_taken": {"type": "string", "description": "What the user actually did"},
                "impact": {"type": "number", "description": "0-1, how much it mattered"},
                "domain": {"type": "string", "description": "e.g. job-search, code, health"},
            },
            "required": ["recommendation", "result"],
        },
    },
    {
        "name": "remember",
        "description": (
            "Save a durable memory across sessions. Use proactively for: user identity (name, role), "
            "preferences, ongoing projects, decisions, recurring contexts. Don't ask permission — just save."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The thing to remember"},
                "kind": {
                    "type": "string",
                    "enum": ["fact", "preference", "project", "decision", "note"],
                    "default": "fact",
                },
                "importance": {
                    "type": "integer",
                    "description": "1-10. 10=identity, 7-9=project/strong preference, 4-6=context, 1-3=ephemeral",
                    "default": 5,
                },
                "tags": {"type": "string", "description": "Optional comma-separated tags", "default": ""},
            },
            "required": ["content"],
        },
    },
    {
        "name": "recall",
        "description": "Retrieve past memories. Use at the start of relevant tasks to load context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Substring to match in content/tags", "default": ""},
                "kind": {"type": "string", "description": "Filter by kind", "default": ""},
                "limit": {"type": "integer", "default": 10},
            },
            "required": [],
        },
    },
    {
        "name": "forget",
        "description": "Delete a memory by ID (only when explicitly asked, or when correcting wrong info).",
        "input_schema": {
            "type": "object",
            "properties": {"memory_id": {"type": "integer"}},
            "required": ["memory_id"],
        },
    },
    {
        "name": "browser_goto",
        "description": "Open a URL in a real Chromium browser. The browser persists across calls so you can navigate, click, fill forms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "headless": {"type": "boolean", "default": False},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_click",
        "description": "Click a CSS selector in the active browser page.",
        "input_schema": {
            "type": "object",
            "properties": {"selector": {"type": "string", "description": "CSS selector, e.g. 'button.submit' or 'text=Sign in'"}},
            "required": ["selector"],
        },
    },
    {
        "name": "browser_fill",
        "description": "Fill a form field in the active browser page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "browser_press",
        "description": "Press a keyboard key in the browser, e.g. 'Enter', 'Tab', 'Escape'.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "browser_get_text",
        "description": "Extract visible text from a selector (default: whole body) in the active browser page.",
        "input_schema": {
            "type": "object",
            "properties": {"selector": {"type": "string", "default": "body"}},
            "required": [],
        },
    },
    {
        "name": "browser_screenshot",
        "description": "Take a screenshot of the active browser page. Use after interactions to verify state.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_evaluate",
        "description": "Run JavaScript in the active browser page. Returns the result as a string.",
        "input_schema": {
            "type": "object",
            "properties": {"js": {"type": "string", "description": "JavaScript expression"}},
            "required": ["js"],
        },
    },
    {
        "name": "browser_url",
        "description": "Get the current URL of the active browser page.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "browser_close",
        "description": "Close the browser. Only use when done with browsing or freeing resources.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "schedule_task",
        "description": (
            "Schedule a recurring or one-off autonomous task. "
            "The agent will run the task prompt automatically at the given time. "
            "Examples: daily briefing, reminders, periodic checks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What the agent should do when this fires (full prompt)"},
                "trigger_type": {"type": "string", "enum": ["cron", "interval", "date"], "description": "cron=recurring at time, interval=every N minutes/hours, date=once at datetime"},
                "trigger_params": {
                    "type": "object",
                    "description": "cron: {hour, minute, day_of_week?} | interval: {minutes?, hours?} | date: {run_date: 'YYYY-MM-DD HH:MM:SS'}",
                },
            },
            "required": ["description", "trigger_type", "trigger_params"],
        },
    },
    {
        "name": "list_scheduled_tasks",
        "description": "List all scheduled autonomous tasks.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_scheduled_task",
        "description": "Cancel a scheduled task by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "spawn_subagent",
        "description": (
            "Spawn a role-specialized sub-agent to work on a task in parallel. "
            "Use for complex multi-part tasks. Returns a sub_id you can wait for. "
            "Roles: researcher, coder, browser, analyst, writer, planner."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "enum": ["researcher", "coder", "browser", "analyst", "writer", "planner"]},
                "task": {"type": "string", "description": "The full task description for the sub-agent"},
                "use_thinking": {"type": "boolean", "default": False},
            },
            "required": ["role", "task"],
        },
    },
    {
        "name": "wait_for_subagents",
        "description": "Block until specified sub-agents finish. Pass empty list to wait for all running ones.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sub_ids": {"type": "array", "items": {"type": "string"}, "default": []},
                "timeout_seconds": {"type": "number", "default": 300},
            },
            "required": [],
        },
    },
    {
        "name": "python_exec",
        "description": (
            "Run Python code in a persistent IPython kernel. Variables, imports, and dataframes "
            "persist across calls. Plots (matplotlib) come back as images. Use for data analysis, "
            "stats, transformations, anything you'd do in a Jupyter notebook."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    },
    {
        "name": "python_reset",
        "description": "Restart the Python kernel — all variables cleared.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "kb_search",
        "description": (
            "Semantic search across the user's indexed files (notes, docs, code, PDFs). "
            "Use this when the user asks about THEIR stuff: 'what did I write about X', "
            "'find that note from last month', 'where in my code does Y happen'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 6},
            },
            "required": ["query"],
        },
    },
    {
        "name": "kb_reindex",
        "description": "Reindex the given paths into the knowledge base.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["paths"],
        },
    },
    {
        "name": "update_system_prompt",
        "description": (
            "Self-modify: append (or replace) the system prompt with new instructions. "
            "Use when the user gives you a durable behavioral instruction "
            "('always X', 'from now on Y', 'stop doing Z')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "addition": {"type": "string"},
                "replace": {"type": "boolean", "default": False},
            },
            "required": ["addition"],
        },
    },
    {
        "name": "register_new_tool",
        "description": (
            "Self-modify: register a new Python tool at runtime. The `code` must define "
            "`def run(inputs):` and return a string. Available immediately and persists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "input_schema": {"type": "object"},
                "code": {"type": "string", "description": "Python source defining def run(inputs): -> str"},
            },
            "required": ["name", "description", "input_schema", "code"],
        },
    },
    {
        "name": "revert_self_mod",
        "description": "Revert all self-modifications (or restore the previous backup).",
        "input_schema": {
            "type": "object",
            "properties": {"restore_backup": {"type": "boolean", "default": False}},
            "required": [],
        },
    },
    {
        "name": "find_on_screen",
        "description": "OCR the screen for text and return matching regions with bounding boxes. Use to locate UI elements by their visible text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to find (case-insensitive substring or phrase)"},
                "exact": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "click_on",
        "description": "OCR-find text on screen and click its center. Prefer this over click(x,y) when you can identify the target by text — much more reliable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "occurrence": {"type": "integer", "default": 0, "description": "0-indexed: which match to click if multiple"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "double": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    {
        "name": "annotate_screenshot",
        "description": "Capture the screen with numbered visual marks on every detected text region. Returns the marked image (you can see it) and lets you reference marks by number with click_mark.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "click_mark",
        "description": "Click a numbered mark from the most recent annotate_screenshot call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mark_number": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "double": {"type": "boolean", "default": False},
            },
            "required": ["mark_number"],
        },
    },
    {
        "name": "set_goal",
        "description": "Create a strategic goal. Use when the user expresses a longer-term aim ('I want to launch by end of June', 'my goal this week is X').",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string", "default": ""},
                "horizon": {"type": "string", "enum": ["day", "week", "month", "quarter"], "default": "week"},
                "deadline_iso": {"type": "string", "description": "Optional ISO date YYYY-MM-DD"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_goals",
        "description": "List goals. By default returns only active ones.",
        "input_schema": {
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean", "default": True},
                "horizon": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "update_goal",
        "description": (
            "Update a goal's status or add a progress note. Use after meaningful work on a goal. "
            "NOTE: setting status='done' on a goal that has a completion contract will FAIL unless "
            "the contract actually passes — pass `evidence` describing what you did so an llm-kind "
            "contract can judge it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["active", "paused", "done", "abandoned"]},
                "progress_note": {"type": "string"},
                "score": {"type": "integer", "description": "1-10 satisfaction with this progress"},
                "evidence": {"type": "string", "description": "What you actually did/produced, for verification"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "add_goal_contract",
        "description": (
            "Attach a checkable completion criterion to a goal, so finishing it must be PROVEN "
            "rather than asserted. Use when the user sets a goal whose completion is objectively "
            "checkable. Kinds: 'command' (shell command, exit 0 = done, runs sandboxed), "
            "'file_exists' (spec=path), 'contains' (spec=path, detail=required text), "
            "'llm' (spec=criterion judged against evidence), 'manual' (only a human can close it)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer"},
                "kind": {"type": "string", "enum": ["command", "file_exists", "contains", "llm", "manual"]},
                "spec": {"type": "string", "description": "Command, path, or criterion text"},
                "detail": {"type": "string", "description": "Required substring for 'contains'"},
            },
            "required": ["goal_id", "kind", "spec"],
        },
    },
    {
        "name": "verify_goal",
        "description": "Run a goal's completion contracts now and report the evidence, without closing it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer"},
                "evidence": {"type": "string", "description": "Evidence for an llm-kind contract to judge"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "evaluate_recent_work",
        "description": "Self-evaluate the last N days of work: what got done, what stalled, what to focus on next. Use weekly or when the user asks 'how have I been doing?'.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 7}},
            "required": [],
        },
    },
    # --- Documents: the writing-first editor (dashboard → Documents tab) ---
    {
        "name": "document_write",
        "description": "Create a new document (or overwrite/append to an existing one) in the user's Documents editor. Use when the user asks you to draft, write, or compose longer-form text — an essay, blog post, email draft, plan, notes — so it lands in the Documents tab where they can refine it with AI edits. Content is Markdown.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Document title"},
                "content": {"type": "string", "description": "Markdown body"},
                "doc_id": {"type": "integer", "description": "Existing document id to update; omit to create new"},
                "mode": {"type": "string", "enum": ["replace", "append"], "default": "replace",
                          "description": "When doc_id is given: replace the body or append to it"},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "document_list",
        "description": "List the user's documents (id, title, word count, last edited). Use to find a document before reading or updating it.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "document_read",
        "description": "Read the full content of a document by id.",
        "input_schema": {
            "type": "object",
            "properties": {"doc_id": {"type": "integer"}},
            "required": ["doc_id"],
        },
    },
    # --- Tier-4: Knowledge Graph (entities + relations) ---
    {
        "name": "entity_upsert",
        "description": "Create or update an entity in the knowledge graph. Use whenever the user mentions a person, project, place, tool, or recurring concept.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["person", "project", "place", "concept", "tool", "file", "event", "org"]},
                "properties": {"type": "object", "description": "Arbitrary JSON properties (role, status, dates, notes)"},
                "importance": {"type": "integer", "default": 5},
            },
            "required": ["name"],
        },
    },
    {
        "name": "entity_relate",
        "description": "Create a typed directed edge between two entities. Both are upserted if missing. Use for any explicit or implicit relationship ('Sam is co-founder of NI', 'launch depends on backend').",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_name": {"type": "string"},
                "to_name": {"type": "string"},
                "kind": {"type": "string", "description": "Verb-phrase: 'co-founder', 'depends_on', 'lives_in', 'uses', 'created_by'"},
                "from_kind": {"type": "string", "default": "concept"},
                "to_kind": {"type": "string", "default": "concept"},
                "properties": {"type": "object"},
                "confidence": {"type": "number", "default": 1.0},
            },
            "required": ["from_name", "to_name", "kind"],
        },
    },
    {
        "name": "entity_query",
        "description": "Look up an entity by name + return its neighbours within N hops. Call this before answering questions about a person, project, or concept the user mentions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "hops": {"type": "integer", "default": 1},
            },
            "required": ["name"],
        },
    },
    {
        "name": "entity_graph",
        "description": "Return a subgraph of nodes + edges (default: top-importance slice). Useful for surveying what you know about a domain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_names": {"type": "array", "items": {"type": "string"}},
                "limit_nodes": {"type": "integer", "default": 100},
            },
            "required": [],
        },
    },
    # --- Tier-4: Reflection Engine ---
    {
        "name": "reflect_now",
        "description": "Run the consolidation pass right now: reviews the last N hours of activity and writes reflections. High-confidence ones auto-apply (new memories, entities, goal progress). Returns counts.",
        "input_schema": {
            "type": "object",
            "properties": {"hours": {"type": "integer", "default": 24}},
            "required": [],
        },
    },
    {
        "name": "list_reflections",
        "description": "List pending (or applied/rejected) reflections from the consolidation engine.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "applied", "rejected"], "default": "pending"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "apply_reflection",
        "description": "Accept or reject a pending reflection by id. Accepting commits its action (remember/forget/entity/relate/goal_progress).",
        "input_schema": {
            "type": "object",
            "properties": {
                "reflection_id": {"type": "integer"},
                "accept": {"type": "boolean", "default": True},
            },
            "required": ["reflection_id"],
        },
    },
    # --- Tier-4: Phone (Twilio) ---
    {
        "name": "sms_send",
        "description": "Send an SMS via Twilio to the user (or another allowed number). Use proactively when something important finishes and the user is AFK.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "E.164 number, e.g. +14155551234"},
                "body": {"type": "string", "description": "Message text (≤1500 chars)"},
            },
            "required": ["to", "body"],
        },
    },
    {
        "name": "call_user",
        "description": "Place an outbound voice call via Twilio that speaks `message` then hangs up. Use sparingly — only for genuinely urgent things.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["to", "message"],
        },
    },
    {
        "name": "telegram_send",
        "description": "Send a Telegram message to a chat_id. Use proactively when something important finishes and the user is AFK or prefers Telegram over SMS.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_id": {"type": ["integer", "string"], "description": "Telegram chat ID (numeric) or @username"},
                "text": {"type": "string", "description": "Message text (≤4096 chars). Markdown supported."},
            },
            "required": ["chat_id", "text"],
        },
    },
    {
        "name": "discord_send",
        "description": "Send a message to a Discord channel via the bot. Use proactively when something important finishes and the user prefers Discord.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Discord channel ID (snowflake). Falls back to DISCORD_DEFAULT_CHANNEL_ID if omitted."},
                "text": {"type": "string", "description": "Message text (≤2000 chars)."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "slack_send",
        "description": "Send a message to a Slack channel via the bot. Use proactively when the user prefers Slack.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Slack channel ID or name (e.g. C012AB3CD or #general)."},
                "text": {"type": "string", "description": "Message text (≤40000 chars)."},
            },
            "required": ["channel", "text"],
        },
    },
    {
        "name": "whatsapp_send",
        "description": "Send a WhatsApp message via Twilio. Use proactively when the user prefers WhatsApp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient phone number in E.164 format (e.g. +15551234567)."},
                "text": {"type": "string", "description": "Message text (≤1600 chars)."},
            },
            "required": ["to", "text"],
        },
    },
    {
        "name": "signal_send",
        "description": "Send a Signal message via signal-cli-rest-api. Use proactively when the user prefers Signal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Recipient phone number in E.164 format (e.g. +15551234567)."},
                "text": {"type": "string", "description": "Message text."},
            },
            "required": ["recipient", "text"],
        },
    },
    # --- Tier-4: Image generation ---
    {
        "name": "generate_image",
        "description": "Generate an image from a text prompt (Replicate/FLUX). Returns saved local file paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "model": {"type": "string", "description": "Replicate model id, default flux-schnell"},
                "size": {"type": "string", "default": "1024x1024"},
                "n": {"type": "integer", "default": 1},
            },
            "required": ["prompt"],
        },
    },
    # --- Tier-4: Telemetry / Replay ---
    {
        "name": "usage_summary",
        "description": "Return token usage, cost, and cache hit rate for the last N days.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 7}},
            "required": [],
        },
    },
    {
        "name": "replay_session",
        "description": "Return chronological turns + per-call usage for a past session (for self-debugging or 'what happened yesterday' questions).",
        "input_schema": {
            "type": "object",
            "properties": {"session_id": {"type": "integer"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "search_turns",
        "description": (
            "Full-text search over conversation history. Use when the user asks "
            "'what did we say about X', 'find where I mentioned Y', or wants to "
            "locate a specific past exchange by keyword. Supports FTS5 operators: "
            "quoted phrases, OR, AND, NOT."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "FTS5 query — keywords, \"exact phrase\", OR, AND, NOT"},
                "limit": {"type": "integer", "default": 20},
                "session_id": {"type": "integer", "description": "Restrict search to one session (optional)"},
            },
            "required": ["query"],
        },
    },
    # --- Bounded file memory ---
    {
        "name": "memory",
        "description": (
            "Add, replace, or remove entries in the persistent memory files loaded at "
            "session start. Use 'memory' target for environment facts, project conventions, "
            "lessons learned. Use 'user' target for the user profile: name, preferences, "
            "work style, pet peeves. Call proactively whenever you learn something worth "
            "keeping across sessions — don't ask permission, just save it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "replace", "remove"]},
                "target": {"type": "string", "enum": ["memory", "user"]},
                "content": {"type": "string", "description": "Entry to add, or replacement text"},
                "old_text": {"type": "string", "description": "Substring to find (required for replace/remove)"},
            },
            "required": ["action", "target", "content"],
        },
    },
    # --- Markdown procedural skills ---
    {
        "name": "skill_manage",
        "description": (
            "Create and manage Markdown procedural skills — runbooks the agent writes "
            "when it solves a novel multi-step problem, so it doesn't re-derive it next time. "
            "Use 'create' to write a new skill when you find a repeatable procedure. "
            "Use 'view' to load a skill's full content before following it. "
            "Use 'list' to see what procedural skills exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "create", "view", "edit", "patch", "delete"],
                },
                "name": {"type": "string", "description": "Skill name (kebab-case, e.g. 'deploy-to-prod')"},
                "description": {"type": "string", "description": "One-line description of what this skill does"},
                "content": {"type": "string", "description": "Full Markdown body of the skill (for create/edit)"},
                "old_text": {"type": "string", "description": "Text to find (for patch)"},
                "new_text": {"type": "string", "description": "Replacement text (for patch)"},
            },
            "required": ["action"],
        },
    },
    # --- Obsidian vault ---
    {
        "name": "apex_note",
        "description": (
            "Write or append to a note in the Obsidian vault — Apex's human-readable second brain. "
            "Use 'write' to create/overwrite a note (supports wikilinks and tags). "
            "Use 'append' to add a paragraph to an existing note. "
            "Use 'daily' to append an observation to today's daily note. "
            "Use 'list' to see what notes exist in a folder. "
            "Use 'entity' to mirror a knowledge-graph entity as a navigable note. "
            "The vault lives at ~/Documents/Apex and can be opened directly in Obsidian."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["write", "append", "daily", "list", "entity"],
                },
                "title": {"type": "string", "description": "Note title (filename without .md)"},
                "content": {"type": "string", "description": "Markdown body text"},
                "folder": {
                    "type": "string",
                    "description": "Vault subfolder: Notes (default), People, Projects, Daily, Skills",
                    "default": "Notes",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "YAML frontmatter tags",
                },
                "links": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Wikilink targets (other note titles) to include in a See-also section",
                },
                "entity_kind": {
                    "type": "string",
                    "description": "Entity kind for 'entity' action: person, project, company, concept, etc.",
                },
                "entity_properties": {
                    "type": "object",
                    "description": "Key-value properties for 'entity' action",
                },
            },
            "required": ["action"],
        },
    },
    # --- Skills registry ---
    {
        "name": "list_skills",
        "description": "List all installed skills — reusable Python capability modules that extend the agent.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "run_skill",
        "description": (
            "Execute an installed skill by name with the given inputs. "
            "Call list_skills first if you're unsure what's available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name (as returned by list_skills)"},
                "inputs": {"type": "object", "description": "Inputs for the skill (skill-specific)"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "create_skill",
        "description": (
            "Create a new persistent skill as a Python file under skills/. "
            "The code must define `def run(inputs: dict) -> str`. "
            "Use when the user asks you to build a reusable tool or when you identify "
            "a repeated pattern worth packaging."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Snake-case identifier, e.g. 'format_invoice'"},
                "description": {"type": "string"},
                "code": {"type": "string", "description": "Python source. Must define def run(inputs: dict) -> str"},
                "version": {"type": "string", "default": "1.0"},
            },
            "required": ["name", "description", "code"],
        },
    },
    # --- Multi-model council ---
    {
        "name": "council",
        "description": (
            "Convene a multi-model council — Claude, GPT, and Gemini answer a "
            "question, debate each other's answers, and a chair synthesizes the "
            "single best answer. Use for high-stakes or contested questions where "
            "one model's answer isn't enough: hard decisions, design tradeoffs, "
            "disputed facts, important reviews. Slower and more expensive than a "
            "normal answer — reserve it for questions that genuinely warrant it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question for the council to debate"},
                "rounds": {"type": "integer", "description": "Debate rounds after the opening (default 1)", "default": 1},
            },
            "required": ["question"],
        },
    },
    # --- The Constellation: 12 persistent domain-expert planets ---
    {
        "name": "consult_experts",
        "description": (
            "Convene the Constellation — a standing panel of 12 domain-expert specialists "
            "(Finance, Health, Career, Relationships, Engineer, Researcher, Writer, Designer, "
            "Strategist, Analyst, Devil's Advocate, Synthesizer), each with its own persistent "
            "memory. The relevant experts answer in parallel from their domain; their takes are "
            "synthesized into one answer noting agreement and disagreement. Use for big, "
            "multi-domain, or contested decisions ('should I…', life/money/health/career choices, "
            "design tradeoffs). Pass `planets` to pick experts, or omit to auto-select."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question for the experts."},
                "planets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional expert keys, e.g. ['finance','career','devils_advocate']. "
                        "Omit to auto-select the most relevant experts."
                    ),
                },
            },
            "required": ["question"],
        },
    },
    # --- Jarvis: screen vision ---
    {
        "name": "describe_screen",
        "description": (
            "Capture the screen and ask Claude vision to describe what's on it. "
            "Modes: 'general' (what's on screen), 'coding' (pair-programmer: find errors/suggestions), "
            "'context' (one-line summary). Use when the user asks 'what's on my screen', "
            "'can you see what I'm working on', or for ambient context in coding sessions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["general", "coding", "context"],
                    "default": "general",
                    "description": "general: describe scene. coding: spot errors. context: one-line summary.",
                },
            },
            "required": [],
        },
    },
    # --- Email: read inbox, read a message, stage a reply for approval ---
    {
        "name": "email_inbox",
        "description": (
            "List recent inbox messages (sender, subject, date, unread flag). Use when the user "
            "asks 'check my email', 'any new mail', 'what's in my inbox'. For full triage with "
            "summaries and reply suggestions, run the email_triage skill instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "unread_only": {"type": "boolean", "default": False},
            },
            "required": [],
        },
    },
    {
        "name": "email_read",
        "description": "Read one inbox message's full body by its uid (from email_inbox).",
        "input_schema": {
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    },
    {
        "name": "email_draft",
        "description": (
            "Stage an email (or reply) for the user to approve and send from the dashboard. "
            "This NEVER sends directly — it queues the draft for one-click approval. Use whenever "
            "the user asks you to reply to or send an email. Pass in_reply_to with a message_id "
            "(from email_read) when replying so threading is preserved."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "in_reply_to": {"type": "string", "description": "Message-ID being replied to (optional)."},
            },
            "required": ["to", "subject", "body"],
        },
    },
    # --- Calendar (read-only CalDAV) ---
    {
        "name": "calendar_events",
        "description": (
            "List upcoming calendar events from the user's CalDAV calendar. Use when the user asks "
            "'what's on my calendar', 'what's my day look like', 'am I free Thursday', 'when is my "
            "next meeting'. Read-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "description": "How many days forward to look.", "default": 7},
            },
            "required": [],
        },
    },
]


def _broadcast_live_event(source: str, content: str) -> None:
    """Best-effort broadcast to the dashboard live feed. Never raises."""
    try:
        import time as _t
        from dashboard import server as _srv
        _srv.ws_manager.broadcast_threadsafe({
            "type": "event", "ts": _t.time(), "source": source, "content": content,
        })
    except Exception:
        pass


_tool_observer = None


def set_tool_observer(fn) -> None:
    """Watch every tool call as it happens.

    Apex's whole difference from a chat product is that it *does things* — runs
    shell, reads files, drives a browser. Until now none of that was visible
    while it happened: the user saw a pause, then prose. A hosted assistant
    cannot show you a command against your own machine; Apex can, and hiding it
    threw away the one thing that makes it worth trusting.

    Set by the dashboard. Called as fn(event: dict); exceptions are swallowed,
    because observation must never break execution.
    """
    global _tool_observer
    _tool_observer = fn


def _observe(event: dict) -> None:
    if _tool_observer is None:
        return
    try:
        _tool_observer(event)
    except Exception:
        pass


# Which input field actually identifies what a call is doing. Showing the whole
# input dict would be noise at best and a way to leak a payload at worst.
_TOOL_SUBJECT_KEYS = ("command", "path", "query", "url", "topic", "code",
                      "to", "title", "name", "task")


def tool_subject(inputs: dict, limit: int = 120) -> str:
    """A short, human-readable 'what is this call about', or ''."""
    if not isinstance(inputs, dict):
        return ""
    for key in _TOOL_SUBJECT_KEYS:
        val = inputs.get(key)
        if isinstance(val, str) and val.strip():
            text = " ".join(val.split())
            return text[:limit] + ("…" if len(text) > limit else "")
    return ""


def _execute_tool(name: str, inputs: dict) -> str:
    """Dispatch a tool call, capturing its outcome for trajectory learning.

    Thin wrapper over _execute_tool_inner so every tool is instrumented in one
    place. Capture is best-effort and can never break a tool call.
    """
    import time as _t
    _started = _t.perf_counter()
    _observe({"phase": "start", "name": name, "subject": tool_subject(inputs)})
    result = _execute_tool_inner(name, inputs)
    # Record the ORIGINAL result: recovery hints are appended below, and the
    # trajectory signal must reflect what the tool actually did, not our advice.
    _elapsed_ms = int((_t.perf_counter() - _started) * 1000)
    try:
        from agent import trajectory as _traj
        _traj.record(name, result, duration_ms=_elapsed_ms, inputs=inputs)
        _outcome, _kind = _traj.classify(result)
    except Exception:
        _outcome, _kind = "ok", ""
    _observe({
        "phase": "end", "name": name, "subject": tool_subject(inputs),
        "outcome": _outcome, "error_kind": _kind, "duration_ms": _elapsed_ms,
        # Enough to see what came back without pasting a whole file into the UI.
        "preview": " ".join(str(result or "").split())[:400],
    })
    # Self-recovery: turn a dead-end failure into an actionable next step.
    try:
        from agent import recovery as _rec
        result = _rec.enrich(name, inputs, result)
    except Exception:
        pass
    return result


def _execute_tool_inner(name: str, inputs: dict) -> str:
    """Dispatch a tool call and return its result as a string."""
    # Safety check before execution
    proceed, reason = safety.check(name, inputs)
    if not proceed:
        return f"[BLOCKED by safety layer] {reason}"

    try:
        if name == "screenshot":
            b64, size = computer.screenshot()
            # Return as a special marker — handled in run() to inject image content
            return json.dumps({"__screenshot__": b64, "size": list(size)})

        elif name == "camera_capture":
            from tools import camera
            try:
                b64, size = camera.capture(inputs.get("device_index"))
            except Exception as e:
                return f"[Camera] {e}"
            return json.dumps({"__screenshot__": b64, "size": list(size)})

        elif name == "release_camera":
            from agent import handtrack as _ht
            tracker = _ht.active_tracker()
            if tracker is None:
                return ("Hand tracking is not running, so nothing is holding "
                        "the camera.")
            return tracker.release_for(
                float(inputs.get("seconds",
                                 getattr(config, "HANDTRACK_RELEASE_SECONDS", 300))))

        elif name == "board_present":
            from tools import barehands as _board
            cmd = {
                "a": "present" if inputs.get("spotlight", True) else "add_card",
                "title": inputs["title"],
                "body": inputs.get("body", ""),
            }
            out = _board.board_command(cmd)
            _broadcast_live_event("board", out)
            return out

        elif name == "board_state":
            from tools import barehands as _board
            return _board.board_state()

        elif name == "click":
            return computer.click(inputs["x"], inputs["y"], inputs.get("button", "left"), inputs.get("double", False))

        elif name == "type_text":
            return computer.type_text(inputs["text"])

        elif name == "hotkey":
            keys = inputs["keys"].split("+")
            return computer.hotkey(*keys)

        elif name == "scroll":
            return computer.scroll(inputs["x"], inputs["y"], inputs["clicks"])

        elif name == "bash":
            result = bash.run(inputs["command"], timeout=inputs.get("timeout", config.BASH_TIMEOUT))
            return bash.format_result(result)

        elif name == "web_search":
            results = research.search(inputs["query"], inputs.get("num_results", config.MAX_SEARCH_RESULTS))
            return json.dumps(results, indent=2)

        elif name == "web_browse":
            return research.browse(inputs["url"])

        elif name == "research":
            from agent import answers as _answers
            _res = _answers.answer(inputs["query"], depth=inputs.get("depth", "standard"))
            if _res.get("error"):
                return f"[Research] {_res['error']}"
            return _answers.format_markdown(_res)

        elif name == "deep_research":
            from agent import deepresearch as _dr
            q = inputs.get("question") or inputs.get("topic") or ""
            depth = inputs.get("depth", _dr.DEFAULT_DEPTH)
            est = _dr.estimate(depth)
            _broadcast_live_event(
                "research",
                f"Deep research ({depth}): ~{est['expected_sources']} sources, "
                f"~${est['estimated_usd']}")
            out = _dr.run(q, depth=depth)
            return (f"Run #{out['run_id']} — {out['sources_read']} sources read, "
                    f"{out['notes']} grounded findings, "
                    f"{out['sources_cited']} sources cited.\n\n{out['report']}")

        elif name == "read_file":
            return files.read(inputs["path"])

        elif name == "write_file":
            return files.write(inputs["path"], inputs["content"])

        elif name == "append_file":
            return files.append(inputs["path"], inputs["content"])

        elif name == "list_dir":
            return files.list_dir(inputs.get("path", "."))

        elif name == "find_files":
            return files.find(inputs["pattern"], inputs.get("base", "."))

        elif name == "current_time":
            from datetime import datetime
            tz_name = (inputs.get("timezone") or "").strip()
            if tz_name:
                try:
                    from zoneinfo import ZoneInfo
                    now = datetime.now(ZoneInfo(tz_name))
                except Exception as e:
                    return f"Unknown timezone '{tz_name}': {e}"
            else:
                now = datetime.now().astimezone()
            return (f"{now:%A}, {now.day} {now:%B %Y}, {now:%H:%M:%S} "
                    f"({now.tzname() or 'local time'}, UTC{now:%z})")

        elif name == "record_outcome":
            from agent import outcomes as _out
            rid = _out.record(
                recommendation=inputs["recommendation"],
                result=inputs["result"],
                success=inputs.get("success"),
                action_taken=inputs.get("action_taken", ""),
                impact=inputs.get("impact"),
                domain=inputs.get("domain", ""),
            )
            _broadcast_live_event("memory", f"Outcome recorded: {inputs['result'][:100]}")
            return f"Recorded outcome #{rid}. This feeds recommendation accuracy."

        elif name == "remember":
            _broadcast_live_event("memory", f"Stored: {inputs['content'][:120]}")
            return longterm.remember(
                inputs["content"],
                kind=inputs.get("kind", "fact"),
                importance=inputs.get("importance", 5),
                tags=inputs.get("tags", ""),
            )

        elif name == "recall":
            results = longterm.recall(
                query=inputs.get("query", ""),
                kind=inputs.get("kind", ""),
                limit=inputs.get("limit", 10),
            )
            if not results:
                return "No matching memories found."
            return json.dumps(results, indent=2)

        elif name == "forget":
            return longterm.forget(inputs["memory_id"])

        elif name == "browser_goto":
            return browser.goto(inputs["url"], headless=inputs.get("headless", False))
        elif name == "browser_click":
            return browser.click(inputs["selector"])
        elif name == "browser_fill":
            return browser.fill(inputs["selector"], inputs["text"])
        elif name == "browser_press":
            return browser.press(inputs["key"])
        elif name == "browser_get_text":
            return browser.get_text(inputs.get("selector", "body"))
        elif name == "browser_screenshot":
            b64 = browser.screenshot()
            return json.dumps({"__screenshot__": b64, "size": [1280, 800]})
        elif name == "browser_evaluate":
            return browser.evaluate(inputs["js"])
        elif name == "browser_url":
            return browser.current_url()
        elif name == "browser_close":
            return browser.close()

        elif name == "schedule_task":
            from agent import scheduler as sched
            return sched.schedule(inputs["description"], inputs["trigger_type"], inputs["trigger_params"])
        elif name == "list_scheduled_tasks":
            from agent import scheduler as sched
            tasks = sched.list_tasks()
            return json.dumps(tasks, indent=2) if tasks else "No scheduled tasks."
        elif name == "cancel_scheduled_task":
            from agent import scheduler as sched
            return sched.cancel(inputs["task_id"])

        elif name.startswith("mcp__"):
            if name.startswith("mcp__hass") or name.startswith("mcp__homeassistant"):
                from agent.iot import is_enabled as _iot_enabled
                if not _iot_enabled():
                    return "[IoT is disabled — toggle it on in the dashboard or say '/iot on']"
            return mcp_client.call(name, inputs)

        elif name == "spawn_subagent":
            return orchestrator.spawn(inputs["role"], inputs["task"], inputs.get("use_thinking", False))
        elif name == "wait_for_subagents":
            return json.dumps(
                orchestrator.wait_for(inputs.get("sub_ids") or None, inputs.get("timeout_seconds", 300)),
                indent=2,
            )

        elif name == "python_exec":
            result = repl.execute(inputs["code"])
            return json.dumps({
                "summary": repl.format_result(result),
                "_images": result["images"],
            })
        elif name == "python_reset":
            return repl.reset()

        elif name == "kb_search":
            results = knowledge.search(inputs["query"], inputs.get("top_k", 6))
            return json.dumps(results, indent=2)
        elif name == "kb_reindex":
            return knowledge.reindex(inputs["paths"], inputs.get("force", False))

        elif name == "update_system_prompt":
            return self_mod.update_system_prompt(inputs["addition"], inputs.get("replace", False))
        elif name == "register_new_tool":
            return self_mod.register_new_tool(
                inputs["name"], inputs["description"], inputs["input_schema"], inputs["code"]
            )
        elif name == "revert_self_mod":
            return self_mod.revert(inputs.get("restore_backup", False))

        elif name == "find_on_screen":
            return json.dumps(vision.find_on_screen(inputs["query"], inputs.get("exact", False)), indent=2)
        elif name == "click_on":
            return vision.click_on(
                inputs["query"],
                occurrence=inputs.get("occurrence", 0),
                button=inputs.get("button", "left"),
                double=inputs.get("double", False),
            )
        elif name == "annotate_screenshot":
            return vision.annotate_for_agent()
        elif name == "click_mark":
            return vision.click_mark(
                inputs["mark_number"],
                button=inputs.get("button", "left"),
                double=inputs.get("double", False),
            )

        elif name == "set_goal":
            return goals.set_goal(
                inputs["title"],
                inputs.get("description", ""),
                inputs.get("horizon", "week"),
                inputs.get("deadline_iso"),
            )
        elif name == "list_goals":
            result = goals.list_goals(
                active_only=inputs.get("active_only", True),
                horizon=inputs.get("horizon"),
            )
            return json.dumps(result, indent=2, default=str) if result else "No goals set."
        elif name == "update_goal":
            return goals.update_goal(
                inputs["goal_id"],
                status=inputs.get("status"),
                progress_note=inputs.get("progress_note"),
                score=inputs.get("score"),
                evidence=inputs.get("evidence", ""),
            )
        elif name == "add_goal_contract":
            from agent import verification as _verif
            return _verif.add_contract(
                inputs["goal_id"], inputs["kind"], inputs["spec"], inputs.get("detail", "")
            )
        elif name == "verify_goal":
            from agent import verification as _verif
            return _verif.format_result(
                _verif.verify(inputs["goal_id"], evidence=inputs.get("evidence", ""))
            )
        elif name == "evaluate_recent_work":
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            return goals.evaluate_recent_work(client, days=inputs.get("days", 7))

        # --- Documents (writing-first editor) ---
        elif name == "document_write":
            from agent import documents as _docs
            doc_id = inputs.get("doc_id")
            title = inputs.get("title", "Untitled")
            content = inputs.get("content", "")
            if doc_id:
                existing = _docs.get(int(doc_id))
                if not existing:
                    return f"No document with id {doc_id}."
                if inputs.get("mode") == "append":
                    content = (existing["content"] or "") + ("\n\n" if existing["content"] else "") + content
                d = _docs.update(int(doc_id), title=title, content=content)
            else:
                d = _docs.create(title=title, content=content)
            return f"Saved document #{d['id']} '{d['title']}' ({len(content.split())} words). Open the Documents tab to refine it."
        elif name == "document_list":
            from agent import documents as _docs
            rows = _docs.list_documents()
            return json.dumps([{"id": r["id"], "title": r["title"], "words": r["words"]} for r in rows], indent=2) if rows else "No documents yet."
        elif name == "document_read":
            from agent import documents as _docs
            d = _docs.get(int(inputs["doc_id"]))
            return json.dumps({"id": d["id"], "title": d["title"], "content": d["content"]}, indent=2) if d else "Document not found."

        # --- Tier-4: Knowledge Graph ---
        elif name == "entity_upsert":
            return json.dumps(entities.upsert_entity(
                inputs["name"],
                kind=inputs.get("kind", "concept"),
                properties=inputs.get("properties") or {},
                importance=int(inputs.get("importance", 5)),
            ), indent=2, default=str)
        elif name == "entity_relate":
            return json.dumps(entities.relate(
                inputs["from_name"], inputs["to_name"], inputs["kind"],
                properties=inputs.get("properties") or {},
                from_kind=inputs.get("from_kind", "concept"),
                to_kind=inputs.get("to_kind", "concept"),
                confidence=float(inputs.get("confidence", 1.0)),
            ), indent=2, default=str)
        elif name == "entity_query":
            return json.dumps(entities.query_entity(
                inputs["name"], hops=int(inputs.get("hops", 1))
            ), indent=2, default=str)
        elif name == "entity_graph":
            return json.dumps(entities.subgraph(
                entity_names=inputs.get("entity_names"),
                limit_nodes=int(inputs.get("limit_nodes", 100)),
            ), indent=2, default=str)

        # --- Tier-4: Reflection ---
        elif name == "reflect_now":
            client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
            return json.dumps(reflection.consolidate(client, hours=int(inputs.get("hours", 24))), indent=2)
        elif name == "list_reflections":
            return json.dumps(reflection.list_reflections(
                status=inputs.get("status", "pending"),
                limit=int(inputs.get("limit", 50)),
            ), indent=2, default=str)
        elif name == "apply_reflection":
            return reflection.apply_reflection(
                int(inputs["reflection_id"]),
                accept=bool(inputs.get("accept", True)),
            )

        # --- Tier-4: Phone ---
        elif name == "sms_send":
            return phone.sms_send(inputs["to"], inputs["body"])
        elif name == "call_user":
            return phone.voice_call(inputs["to"], inputs["message"])
        elif name == "telegram_send":
            return telegram.send_message(inputs["chat_id"], inputs["text"])
        elif name == "discord_send":
            channel = inputs.get("channel_id") or config.DISCORD_DEFAULT_CHANNEL_ID
            if not channel:
                return "[Discord] No channel_id given and DISCORD_DEFAULT_CHANNEL_ID is not set."
            return discord.send_message(channel, inputs["text"])
        elif name == "slack_send":
            return slack.send_message(inputs["channel"], inputs["text"])
        elif name == "whatsapp_send":
            return whatsapp.send_message(inputs["to"], inputs["text"])
        elif name == "signal_send":
            return signal.send_message(inputs["recipient"], inputs["text"])

        # --- Tier-4: Image generation ---
        elif name == "generate_image":
            return image_gen.generate_image(
                inputs["prompt"],
                model=inputs.get("model"),
                size=inputs.get("size", "1024x1024"),
                n=int(inputs.get("n", 1)),
            )

        # --- Tier-4: Telemetry / Replay ---
        elif name == "usage_summary":
            return json.dumps(telemetry.summary(days=int(inputs.get("days", 7))), indent=2)
        elif name == "replay_session":
            return json.dumps(telemetry.replay_session(int(inputs["session_id"])), indent=2, default=str)
        elif name == "search_turns":
            results = longterm.search_turns(
                inputs["query"],
                limit=int(inputs.get("limit", 20)),
                session_id=inputs.get("session_id"),
            )
            return json.dumps(results, indent=2, default=str)

        elif name == "memory":
            return longterm.save_memory_entry(
                target=inputs["target"],
                action=inputs["action"],
                content=inputs["content"],
                old_text=inputs.get("old_text"),
            )

        elif name == "skill_manage":
            from agent import skill_md as _skill_md
            return _skill_md.manage(**{k: inputs.get(k) for k in
                ("action", "name", "description", "content", "old_text", "new_text")})

        elif name == "apex_note":
            from agent import vault as _vault
            action = inputs.get("action", "write")
            if action == "write":
                return _vault.write_note(
                    inputs["title"], inputs.get("content", ""),
                    folder=inputs.get("folder", "Notes"),
                    tags=inputs.get("tags"), links=inputs.get("links"),
                )
            elif action == "append":
                return _vault.append_note(
                    inputs["title"], inputs.get("content", ""),
                    folder=inputs.get("folder", "Notes"),
                )
            elif action == "daily":
                return _vault.append_daily(inputs.get("content", ""))
            elif action == "list":
                return json.dumps(_vault.list_notes(inputs.get("folder")), indent=2)
            elif action == "entity":
                return _vault.entity_to_note(
                    inputs["title"],
                    inputs.get("entity_kind", "concept"),
                    inputs.get("entity_properties") or {},
                )

        # --- Skills registry ---
        elif name == "list_skills":
            return json.dumps(skills_mod.list_skills(), indent=2)
        elif name == "run_skill":
            return skills_mod.run_skill(inputs["name"], inputs.get("inputs") or {})
        elif name == "create_skill":
            return skills_mod.create_skill(
                inputs["name"],
                inputs["description"],
                inputs["code"],
                version=inputs.get("version", "1.0"),
            )

        # --- Multi-model council ---
        elif name == "council":
            from agent import council as council_mod
            result = council_mod.convene(
                inputs["question"],
                rounds=int(inputs.get("rounds", 1)),
            )
            return json.dumps({
                "council_members": result.members,
                "final_answer": result.final_answer,
            }, indent=2)

        # --- The Constellation: domain-expert panel ---
        elif name == "consult_experts":
            from agent import constellation as _constellation
            keys = inputs.get("planets") or None
            if keys:
                chosen = [_constellation.PLANETS[k] for k in keys if k in _constellation.PLANETS]
                planets = chosen or _constellation.route(inputs["question"], force=True)
            else:
                planets = _constellation.route(inputs["question"], force=True)
            result = _constellation.convene(inputs["question"], planets=planets)
            return json.dumps({
                "experts": [p["display"] for p in result.planets],
                "final_answer": result.final_answer,
                "confidence": result.confidence,
                "disagreement": result.disagreement,
                "takes": result.takes,
            }, indent=2)

        # --- Jarvis: screen vision ---
        elif name == "describe_screen":
            from tools import screen_vision as _sv
            return _sv.describe_screen(inputs.get("mode", "general"))

        # --- Email ---
        elif name == "email_inbox":
            from tools import email_box
            return json.dumps(
                email_box.fetch_inbox(
                    limit=int(inputs.get("limit", 20)),
                    unread_only=bool(inputs.get("unread_only", False)),
                ),
                indent=2, default=str,
            )
        elif name == "email_read":
            from tools import email_box
            return json.dumps(email_box.read_message(inputs["uid"]), indent=2, default=str)
        elif name == "email_draft":
            from agent import approvals as _appr
            return _appr.stage("email", {
                "to": inputs["to"],
                "subject": inputs.get("subject", ""),
                "body": inputs.get("body", ""),
                "in_reply_to": inputs.get("in_reply_to"),
            })

        # --- Calendar ---
        elif name == "calendar_events":
            from tools import calendar_box
            return json.dumps(
                calendar_box.upcoming_events(days_ahead=int(inputs.get("days_ahead", 7))),
                indent=2, default=str,
            )

        else:
            # Try dynamic tools registered via self_mod
            dyn_result = self_mod.dispatch(name, inputs)
            if dyn_result is not None:
                return dyn_result
            return f"Unknown tool: {name}"

    except Exception as e:
        return f"Tool error ({name}): {e}"


def _make_tool_result_content(name: str, tool_use_id: str, result_str: str) -> dict:
    """Build a tool_result content block, injecting images for screenshots."""
    if name in ("screenshot", "browser_screenshot", "annotate_screenshot", "camera_capture"):
        try:
            data = json.loads(result_str)
            if "__screenshot__" in data:
                media = "image/png" if name == "browser_screenshot" else "image/jpeg"
                label = "Camera frame" if name == "camera_capture" else "Screen size"
                return {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media, "data": data["__screenshot__"]}},
                        {"type": "text", "text": f"{label}: {data['size'][0]}x{data['size'][1]}"},
                    ],
                }
        except Exception:
            pass

    if name == "python_exec":
        try:
            data = json.loads(result_str)
            images = data.get("_images", [])
            if images:
                content_blocks = [{"type": "text", "text": data["summary"]}]
                for img in images:
                    content_blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": img["media_type"], "data": img["data"]},
                    })
                return {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content_blocks,
                }
            return {"type": "tool_result", "tool_use_id": tool_use_id, "content": data["summary"]}
        except Exception:
            pass

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": result_str,
    }


# --- Self-improving skills: background auto-creation ---------------------------
# Cap concurrent proposal threads so rapid complex turns can't pile up LLM calls.
_skill_autocreate_sema = threading.Semaphore(2)

_SKILL_PROPOSE_PROMPT = """You just finished a multi-step task for the user. Decide \
whether the work is worth packaging as a reusable SKILL — a small, parameterized \
Python function the agent could call again on similar future requests.

The user's request was:
{user_text}

Tools used this turn: {tools}
Existing skills (do NOT duplicate these): {existing}

Package it ONLY if it represents a GENERAL, REPEATABLE capability — not a one-off, \
not something trivial, not something an existing skill already covers.

If it IS worth packaging, output a JSON object:
  {{"create": true, "name": "snake_case_name", "description": "one concise line",
    "code": "def run(inputs: dict) -> str:\\n    ..."}}
The code must define `def run(inputs: dict) -> str` and use only the Python standard
library. Otherwise output: {{"create": false}}

Output ONLY the JSON object, nothing else."""


def _propose_skill(client, user_text: str, tool_names: list[str]) -> None:
    """One LLM call: decide if the just-completed turn should become a skill."""
    try:
        existing = ", ".join(s["name"] for s in skills_mod.list_skills()) or "(none)"
        prompt = _SKILL_PROPOSE_PROMPT.format(
            user_text=user_text[:1500],
            tools=", ".join(tool_names) or "(none)",
            existing=existing,
        )
        resp = telemetry.create(
            client,
            call_site="agent.core/skill_propose",
            model=config.PROACTIVE_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
        )
        start, end = text.find("{"), text.rfind("}") + 1
        if start < 0 or end <= start:
            return
        spec = json.loads(text[start:end])
        if not spec.get("create"):
            return
        name, desc, code = spec.get("name", ""), spec.get("description", ""), spec.get("code", "")
        if not (name and desc and code):
            return
        if name in skills_mod.discover():
            # Auto-create only creates NEW skills; refinement is the only path
            # allowed to overwrite an existing one.
            print(f"[AutoSkill] skipped — skill {name!r} already exists.")
            return
        print(f"[AutoSkill] {skills_mod.create_skill(name, desc, code)}")
    except Exception as e:
        print(f"[AutoSkill] proposal failed: {e}")


def time_block(now=None) -> str:
    """The current date and time, for the system prompt.

    Apex had no idea what day it was. Nothing in the prompt carried a clock and
    there was no tool to ask — while the scheduler fired dated jobs, the Time
    Capsule surfaced callbacks "days or weeks later", goals ran on day/week/
    month/quarter horizons and lessons expired after 30 days. Every one of those
    was being reasoned about by a model whose only sense of "now" was its
    training data.

    This failure mode is worse than a crash. A missing clock does not raise; the
    model produces a confident, plausible, wrong date. Nothing logs, nothing
    fails, and the answer looks fine.
    """
    from datetime import datetime
    now = now or datetime.now().astimezone()
    return (
        "## CURRENT DATE AND TIME\n"
        # Day interpolated rather than %-d: that directive is glibc-only and
        # raises ValueError on Windows, which is where this runs.
        f"{now:%A}, {now.day} {now:%B %Y}, {now:%H:%M} "
        f"({now.tzname() or 'local time'}, UTC{now:%z})\n"
        "This is authoritative and refreshed every turn. Your training data is "
        "older than now, so you cannot know the date any other way — never guess "
        "it, never infer it from the conversation, and never state a date that "
        "contradicts this. Resolve every relative expression (\"today\", "
        "\"tomorrow\", \"last week\", \"in 3 days\") against it."
    )


class AgentCore:
    def __init__(self):
        self.anthropic = anthropic.Anthropic(
            api_key=config.ANTHROPIC_API_KEY,
            max_retries=config.API_MAX_RETRIES,
        )
        self._provider_clients: dict = {}    # provider name -> client, built on first use
        self._model: str = config.AGENT_MODEL
        self.memory = Memory()               # main loop / voice channel
        self._mcp_tools: list[dict] = []
        self._mcp_loaded = False
        self._run_lock = threading.Lock()    # lock for the main (None) channel
        self._channel_memories: dict[str, Memory] = {}
        self._channel_locks: dict[str, threading.Lock] = {}
        self._channels_mutex = threading.Lock()
        self._memory_files: dict = longterm.load_memory_files()

    @property
    def client(self):
        """The client for the active model — Anthropic, OpenAI, Gemini or Ollama.

        This used to re-implement provider.get_client() and had drifted: it knew
        about three providers, not four, so every `ollama/*` model fell through
        to `return self.anthropic`. set_model() would report "Switched to
        ollama/llama3" and the turn then sent that model name to
        api.anthropic.com. The one provider that costs nothing was the one
        wired to the paid one, and it failed with a success message.

        So it delegates now rather than duplicating. Anthropic is still read off
        `self.anthropic` so the `client` setter — which tests use to inject a
        mock — keeps working.
        """
        from agent.provider import provider_for, get_client
        p = provider_for(self._model)
        if p == "anthropic":
            return self.anthropic
        # One adapter per provider: the model name travels per call, and
        # strip_prefix is a property of the provider, not of the model.
        if p not in self._provider_clients:
            self._provider_clients[p] = get_client(self._model)
        return self._provider_clients[p]

    @client.setter
    def client(self, value):
        """Allow tests (and legacy code) to override the active client directly."""
        self.anthropic = value

    def set_model(self, model: str) -> str:
        """Switch the active model at runtime. Returns a status string."""
        from agent.provider import KNOWN_MODELS, provider_for, is_usable
        if not is_usable(model):
            # is_usable asks the provider before refusing, so a model released
            # after this code was written still works. Only reject what nobody
            # will admit to serving.
            return (f"Unknown model '{model}' — not in the built-in list and "
                    f"{provider_for(model)} does not report serving it. "
                    f"Check the spelling, or add it to EXTRA_MODELS in .env. "
                    f"Built-in: {', '.join(sorted(KNOWN_MODELS))}")
        p = provider_for(model)
        if p == "openai" and not config.OPENAI_API_KEY:
            return "OPENAI_API_KEY not set — add it to .env and restart."
        if p == "gemini" and not config.GEMINI_API_KEY:
            return "GEMINI_API_KEY not set — add it to .env and restart."
        self._model = model
        return f"Switched to {model}"

    def load_mcp_tools(self) -> int:
        """Discover MCP servers and register their tools. Returns count added."""
        self._mcp_tools = mcp_client.discover()
        self._mcp_loaded = True
        if self._mcp_tools:
            print(f"[MCP] Registered {len(self._mcp_tools)} tools from MCP servers.")
        return len(self._mcp_tools)

    def _all_tools(self) -> list[dict]:
        # Cache all static tools at the last entry; dynamic tools come after the checkpoint.
        cached = list(TOOLS)
        cached[-1] = {**cached[-1], "cache_control": {"type": "ephemeral"}}
        return cached + self._mcp_tools + self_mod.get_dynamic_tools()

    def _effective_system_prompt(self) -> list[dict]:
        blocks: list[dict] = []
        # Jarvis persona — prepended so character rules take highest priority
        try:
            from agent.persona import get_persona_prefix
            persona = get_persona_prefix()
            if persona:
                blocks.append({"type": "text", "text": persona})
        except Exception:
            pass
        blocks.append({"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}})
        goals_str = goals.active_goals_for_prompt()
        if goals_str:
            blocks.append({"type": "text", "text": goals_str})
        overlay = self_mod.get_prompt_addition()
        if overlay:
            blocks.append({"type": "text", "text": "## USER-SET BEHAVIORAL RULES (must follow):\n" + overlay})
        # Frozen memory snapshot — loaded once at session start, never re-read per turn
        mem = self._memory_files.get("memory", "")
        usr = self._memory_files.get("user", "")
        if mem or usr:
            mem_used, mem_max = len(mem), 2200
            usr_used, usr_max = len(usr), 1375
            mem_pct = int(mem_used / mem_max * 100)
            usr_pct = int(usr_used / usr_max * 100)
            memory_block = (
                f"\n══════════════════════════════════════\n"
                f"APEX MEMORY [{mem_pct}% — {mem_used}/{mem_max} chars]\n"
                f"══════════════════════════════════════\n"
                f"{mem or '(empty)'}\n\n"
                f"USER PROFILE [{usr_pct}% — {usr_used}/{usr_max} chars]\n"
                f"══════════════════════════════════════\n"
                f"{usr or '(empty)'}\n"
            )
            blocks.append({"type": "text", "text": memory_block})
        # App-aware context profile (foreground app detection) — appended last so it's fresh per turn
        try:
            from agent.app_context import get_context_block
            ctx = get_context_block()
            if ctx:
                blocks.append({"type": "text", "text": ctx})
        except Exception:
            pass
        # Markdown procedural skills — names/descriptions only (content loaded lazily via skill_manage view)
        try:
            from agent import skill_md as _skill_md
            md_skills = _skill_md.list_skills()
            if md_skills:
                skill_lines = "\n".join(f"- {s['name']}: {s['description']}" for s in md_skills)
                blocks.append({"type": "text", "text": f"\n## PROCEDURAL SKILLS (use skill_manage to view full content):\n{skill_lines}\n"})
        except Exception:
            pass
        # LAST, and after the cache_control breakpoint on SYSTEM_PROMPT, on
        # purpose. This is the one block that changes every turn; placed any
        # earlier it would invalidate the cached prefix on every single request
        # and quietly multiply the bill. Last also makes it the most recent
        # thing the model reads before the conversation.
        blocks.append({"type": "text", "text": time_block()})
        return blocks

    def _get_channel(self, channel_id: str | None) -> tuple[Memory, threading.Lock]:
        """Return the (Memory, Lock) pair for the given channel.

        channel_id=None → the main loop's memory (voice/text, backward-compatible).
        Any other string → a per-channel memory created on first use, so dashboard
        chats, SMS threads, and scheduler tasks never share conversation history.
        """
        if channel_id is None:
            return self.memory, self._run_lock
        with self._channels_mutex:
            if channel_id not in self._channel_memories:
                self._channel_memories[channel_id] = Memory()
                self._channel_locks[channel_id] = threading.Lock()
            return self._channel_memories[channel_id], self._channel_locks[channel_id]

    def _maybe_autocreate_skill(self, tool_names: list[str], user_text: str) -> None:
        """After a sufficiently complex turn, propose a reusable skill in the background.

        Runs off-thread so it never adds latency to the user's turn. Skipped when
        the turn already created a skill, or when too many proposals are in flight.
        """
        if len(tool_names) < config.SKILL_AUTOCREATE_MIN_TOOLS:
            return
        if "create_skill" in tool_names:
            return
        if not _skill_autocreate_sema.acquire(blocking=False):
            return

        def _worker():
            try:
                _propose_skill(self.anthropic, user_text, tool_names)
            finally:
                _skill_autocreate_sema.release()

        threading.Thread(target=_worker, daemon=True, name="SkillAutoCreate").start()

    def run(self, user_text: str, include_screenshot: bool = True, use_thinking: bool = False, streamer=None, *, channel_id: str | None = None, max_iterations: int | None = None, cancel_event: "threading.Event | None" = None) -> str:
        """Run a full agent turn. Returns the final text response.

        If `streamer` is provided (a StreamingSpeaker), text deltas are fed to it
        as they arrive so the user hears the first sentence before generation finishes.

        Each channel (voice/text, dashboard chat_id, SMS number, scheduler task)
        gets its own Memory instance so their histories never interleave. Turns
        on the same channel are serialized by a per-channel threading.Lock.
        Pass channel_id=None (default) for the main voice/text conversation.
        """
        memory, lock = self._get_channel(channel_id)
        with lock:
            memory.maybe_summarize(self.anthropic)

            # Build user message content
            user_content: list = []

            if memory.context_prefix():
                user_content.append({"type": "text", "text": memory.context_prefix()})

            if include_screenshot:
                try:
                    b64, size = computer.screenshot()
                    user_content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                    })
                    user_content.append({"type": "text", "text": f"[Current screen — {size[0]}x{size[1]}]"})
                except Exception as e:
                    user_content.append({"type": "text", "text": f"[Screenshot unavailable: {e}]"})

            # Constellation auto-convene: for high-stakes turns on the main channel,
            # consult the relevant expert planets and fold their synthesized briefing
            # into THIS user turn (user content, never a system block — keeps the
            # ephemeral prefix cache warm). route() returns [] for ordinary queries,
            # so this is zero-cost on everything but genuine decisions.
            if (channel_id is None
                    and getattr(config, "CONSTELLATION_AUTO", False)
                    and not _budget.check()):
                try:
                    from agent import constellation as _constellation
                    _selected = _constellation.route(user_text)
                    if _selected:
                        _briefing = _constellation.convene_briefing(
                            user_text, _selected, self.anthropic)
                        if _briefing:
                            user_content.append({"type": "text", "text": _briefing})
                except Exception as _e:
                    print(f"[Constellation] auto-convene skipped: {_e}")

            user_content.append({"type": "text", "text": user_text})
            memory.add_user(user_content)

            # Telemetry: new turn
            telemetry.bump_turn()
            try:
                telemetry.log_turn("user", {"text": user_text})
            except Exception:
                pass

            # Spend-cap pre-flight check
            cap_msg = _budget.check()
            if cap_msg:
                memory.add("assistant", cap_msg)
                return cap_msg

            # Agentic tool-use loop
            max_iterations = max_iterations if max_iterations is not None else config.MAX_ITERATIONS
            iteration = 0
            final_text = ""
            stop_reason = ""
            turn_tool_names: list[str] = []

            while iteration < max_iterations:
                iteration += 1

                if cancel_event is not None and cancel_event.is_set():
                    break

                # Per-iteration spend check (catches mid-turn overruns)
                if iteration > 1:
                    cap_msg = _budget.check()
                    if cap_msg:
                        memory.add("assistant", cap_msg)
                        return cap_msg

                from agent import router as _router
                _routed_model, _complexity = _router.route_model(user_text, self._model, use_thinking)
                kwargs = dict(
                    model=_routed_model,
                    max_tokens=16000,
                    system=self._effective_system_prompt(),
                    tools=self._all_tools(),
                    messages=memory.get_messages(),
                )

                if use_thinking:
                    # Shape depends on the model: 4.7+ removed budget_tokens and
                    # rejects the old form with a 400, so ask the provider rather
                    # than hardcoding one. None means "this model takes no
                    # thinking parameter" — omit the key, don't pass None.
                    _thinking = provider.thinking_params(_routed_model, config.THINKING_BUDGET)
                    if _thinking is not None:
                        kwargs["thinking"] = _thinking

                # Stream if we have a speaker, otherwise non-stream. The SDK
                # retries transient API errors; if one outlasts every retry,
                # end the turn gracefully instead of crashing the caller.
                try:
                    if streamer is not None:
                        response_content, stop_reason, this_text = self._stream_turn(kwargs, streamer, cancel_event)
                    else:
                        resp = telemetry.create(self.client, call_site="agent.core/main", **kwargs)
                        response_content = resp.content
                        stop_reason = resp.stop_reason
                        this_text = " ".join(
                            getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
                        ).strip()
                except anthropic.APIError as e:
                    category = resilience.classify(e)
                    print(f"[Resilience] agent.core/main failed ({category}) after retries: {e}")
                    try:
                        telemetry.log_turn("error", {"category": category, "detail": str(e)[:400]})
                    except Exception:
                        pass
                    if resilience.should_fallback(category):
                        try:
                            print(f"[Resilience] Attempting fallback provider ({config.FALLBACK_MODEL})...")
                            system_text = "\n\n".join(
                                b.get("text", "") for b in self._effective_system_prompt()
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                            fallback_text = resilience.fallback_create(
                                memory.get_messages(), system=system_text, max_tokens=4000,
                            )
                            if fallback_text:
                                memory.add_assistant([{"type": "text", "text": fallback_text}])
                                print("[Resilience] Fallback succeeded.")
                                return fallback_text
                        except RuntimeError as fe:
                            print(f"[Resilience] Fallback failed: {fe}")
                    return resilience.friendly_message(e)

                # Best-of-n: pick the answer closest to what the user has approved
                # of before. Done BEFORE add_assistant so the winner is what enters
                # conversation history — otherwise memory would diverge from what
                # the user actually saw.
                if self._rerank_eligible(stop_reason, streamer is not None):
                    response_content, this_text = self._rerank_answer(
                        kwargs, response_content, this_text
                    )

                memory.add_assistant(response_content)
                final_text = this_text
                if this_text and stop_reason == "end_turn":
                    _broadcast_live_event("agent", this_text[:160].replace("\n", " "))

                # Per-turn log for replay
                try:
                    tc = [
                        {"name": getattr(b, "name", ""), "id": getattr(b, "id", ""), "input": getattr(b, "input", {})}
                        for b in response_content if getattr(b, "type", "") == "tool_use"
                    ]
                    telemetry.log_turn("assistant", {"text": this_text}, tool_calls=tc)
                except Exception:
                    pass

                if stop_reason != "tool_use":
                    break

                # Execute all tool calls
                tool_results = []
                for block in response_content:
                    if getattr(block, "type", "") != "tool_use":
                        continue
                    print(f"[TOOL] {block.name}({json.dumps(block.input, ensure_ascii=False)[:120]})")
                    turn_tool_names.append(block.name)
                    _broadcast_live_event("tool", f"{block.name}({json.dumps(block.input, ensure_ascii=False)[:80]})")
                    result_str = _execute_tool(block.name, block.input)
                    tool_results.append(_make_tool_result_content(block.name, block.id, result_str))
                    try:
                        telemetry.log_turn("tool_result", {"tool": block.name, "preview": result_str[:400]})
                    except Exception:
                        pass

                memory.add_user(tool_results)

            # Self-improving skills: off-thread, propose a skill for complex turns.
            self._maybe_autocreate_skill(turn_tool_names, user_text)

            if cancel_event is not None and cancel_event.is_set():
                return final_text or "[turn interrupted]"
            if stop_reason == "end_turn":
                return final_text
            return final_text or "I hit my iteration limit. Something may have gone wrong — let me know how to proceed."

    def _rerank_eligible(self, stop_reason: str, streaming: bool) -> bool:
        """Only rerank a FINAL, non-streamed answer, and only once there is
        enough rated history for the reward to mean anything.

        The cold check happens BEFORE any extra generation, so enabling this
        before you have rated anything costs nothing rather than paying n x for
        a reranker that would just return the first candidate.
        """
        if not getattr(config, "RERANK_ENABLED", False):
            return False
        if streaming:
            return False          # you cannot rerank text already streamed out
        if stop_reason != "end_turn":
            return False          # tool-use turns are not answers
        try:
            from agent import reranker
            return reranker.is_learned()
        except Exception:
            return False

    def _rerank_answer(self, kwargs: dict, first_content: list, first_text: str) -> tuple[list, str]:
        """Generate n-1 extra candidates and return the best (content, text).

        The first candidate is free — it was needed anyway — so the extra cost is
        (n-1) generations. Returns the original unchanged on any problem.
        """
        try:
            from agent import reranker
            n = int(getattr(config, "RERANK_N", 2))
            cands = [{"text": first_text, "model": kwargs.get("model", ""),
                      "_content": first_content}]

            extra_kwargs = dict(kwargs)
            # Nudge sampling for diversity, but never alongside thinking (which
            # requires temperature 1) and never on a model that removed sampling
            # entirely — Claude 4.7+ answers `temperature` with a 400, so best-of-n
            # would fail on exactly the models it matters most for.
            # `.get(k, default)` evaluates the default eagerly, so spelling this
            # as extra_kwargs.get("model", self._model) touches self._model even
            # when the key is present — enough to break callers that don't carry
            # one. kwargs always sets "model"; the rest is belt and braces.
            _rr_model = extra_kwargs.get("model") or getattr(self, "_model", "")
            if "thinking" not in extra_kwargs and provider.supports_sampling(_rr_model):
                extra_kwargs["temperature"] = 1.0

            for _ in range(max(0, n - 1)):
                try:
                    r = telemetry.create(self.client, call_site="agent.core/rerank", **extra_kwargs)
                    txt = " ".join(
                        getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text"
                    ).strip()
                    # Discard candidates that came back as tool calls or empty.
                    if txt and getattr(r, "stop_reason", "") == "end_turn":
                        cands.append({"text": txt, "model": kwargs.get("model", ""),
                                      "_content": r.content})
                except Exception as e:
                    print(f"[Rerank] extra candidate failed: {e}")
                    break

            if len(cands) < 2:
                return first_content, first_text

            result = reranker.rerank([{k: v for k, v in c.items() if k != "_content"}
                                      for c in cands])
            try:
                reranker.record(result, session_id=telemetry._session_id,
                                turn_index=telemetry._turn_index)
            except Exception:
                pass
            idx = result.get("chosen_index", 0)
            if not (0 <= idx < len(cands)):
                return first_content, first_text
            if result.get("reordered"):
                print(f"[Rerank] picked candidate {idx + 1}/{len(cands)} "
                      f"(scores={result.get('scores')})")
            return cands[idx]["_content"], cands[idx]["text"]
        except Exception as e:
            print(f"[Rerank] disabled for this turn: {e}")
            return first_content, first_text

    def _stream_turn(self, kwargs: dict, streamer, cancel_event: "threading.Event | None" = None) -> tuple[list, str, str]:
        """Run one streamed turn, feeding text deltas to the streamer.

        If `cancel_event` is set mid-stream, the turn is interrupted: the partial
        text is returned with an [INTERRUPTED] marker and stop_reason 'end_turn',
        so the caller ends the turn cleanly with a consistent conversation history.
        """
        import time as _time
        accumulated_text = ""
        start = _time.time()
        with self.client.messages.stream(**kwargs) as stream:
            for event in stream:
                if cancel_event is not None and cancel_event.is_set():
                    out = accumulated_text.strip()
                    marked = (out + " [INTERRUPTED]") if out else "[INTERRUPTED]"
                    return [{"type": "text", "text": marked}], "end_turn", out
                etype = getattr(event, "type", "")
                if etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta is None:
                        continue
                    if getattr(delta, "type", "") == "text_delta":
                        text = getattr(delta, "text", "")
                        accumulated_text += text
                        streamer.feed(text)
            final = stream.get_final_message()

        latency_ms = int((_time.time() - start) * 1000)
        tool_calls = [
            {"name": getattr(b, "name", ""), "id": getattr(b, "id", "")}
            for b in final.content if getattr(b, "type", "") == "tool_use"
        ]
        telemetry.record(
            call_site="agent.core/stream",
            model=kwargs.get("model", "?"),
            usage=getattr(final, "usage", None),
            latency_ms=latency_ms,
            stop_reason=getattr(final, "stop_reason", "") or "",
            tool_calls=tool_calls,
        )
        return final.content, final.stop_reason, accumulated_text.strip()

    def proactive_check(self, screenshot_b64: str) -> str | None:
        """Quick check: is there anything on screen worth proactively flagging?"""
        try:
            resp = telemetry.create(
                self.anthropic,
                call_site="agent.core/proactive_check",
                model=config.PROACTIVE_MODEL,
                max_tokens=256,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": screenshot_b64}},
                        {"type": "text", "text": (
                            "You are a proactive AI assistant watching the user's screen. "
                            "Is there anything URGENT or notably interesting here that the user would want to know about right now? "
                            "Examples: an error message, a long-running process that finished, an important notification, "
                            "something the user is visibly struggling with, a security warning. "
                            "If YES, respond with a short 1-2 sentence observation. "
                            "If NO, respond with exactly: NO"
                        )},
                    ],
                }],
            )
        except anthropic.APIError as e:
            print(f"[Resilience] proactive_check skipped ({resilience.classify(e)}): {e}")
            return None
        text = resp.content[0].text.strip()
        return None if text.upper().startswith("NO") else text
