"""
Multi-turn agentic evals: how many tool-calling turns does a model need to
finish a task, and what does the whole trajectory cost?

Why turns matter for cost: every turn re-sends the conversation so far, so
input tokens grow roughly quadratically with turn count. A model that needs
6 turns instead of 3 doesn't cost 2x — it can cost 3-4x, and it adds the
latency of three extra round-trips. This harness measures that directly.

Each task gives the model a goal and a set of tools with deterministic mock
backends. The loop executes tool calls and feeds results back until the model
answers in text, it exceeds MAX_TURNS, or errors. Success is checked against
the task's known ground truth.

Usage:
  python quality/agentic_evals.py --backend mantle --model openai.gpt-5.6-luna --effort none
  python quality/agentic_evals.py --backend saas --model gpt-5.4-mini --repeats 5
"""

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone

from quick_evals import make_client, call_cost_usd, capture_error

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
MAX_TURNS = 12
MAX_OUTPUT_TOKENS = 2048


def tool(name, description, properties, required=None):
    return {
        "type": "function", "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required or list(properties),
                       "additionalProperties": False},
        "strict": True,
    }


# ─────────────────────────────────────────────────────────────────── tasks ──
# Every tool backend is a pure function over static data => runs are
# deterministic and every task has one verifiable answer.

CUSTOMERS = {"Acme Corp": "C-1001", "Globex": "C-1002", "Initech": "C-1003"}
ORDERS = {
    "C-1001": ["O-1", "O-2", "O-3"],
    "C-1002": ["O-4"],
    "C-1003": ["O-5", "O-6"],
}
ORDER_TOTALS = {"O-1": 1200.50, "O-2": 340.00, "O-3": 89.99, "O-4": 5000.00,
                "O-5": 220.10, "O-6": 179.90}

WEATHER = {"berlin": {"temp_c": 14, "condition": "rain"},
           "madrid": {"temp_c": 28, "condition": "sunny"},
           "oslo": {"temp_c": 9, "condition": "cloudy"}}

FILES = {
    "app/config.yaml": "log_level: info\nregion: eu-central-1\nmax_workers: 16\n",
    "app/main.py": "import yaml\n# loads config.yaml at startup\n",
    "docs/readme.md": "See app/config.yaml for deployment settings.\n",
}

TICKETS = {
    "T-1": {"status": "open", "priority": "low", "assignee": None},
    "T-2": {"status": "open", "priority": "critical", "assignee": None},
    "T-3": {"status": "closed", "priority": "high", "assignee": "sam"},
}

KG = {  # tiny knowledge graph for multi-hop lookup
    "Voyager 1": {"launched_by": "NASA", "launch_year": "1977"},
    "NASA": {"founded": "1958", "headquarters": "Washington, D.C."},
    "Washington, D.C.": {"population_millions": "0.7"},
}

# ── hard-suite data ──────────────────────────────────────────────────────────

# deploy_chain: topological-order deployment. Deploying 'metrics' requires its
# full dependency closure — 8 services. The backend enforces ordering, so a
# model that doesn't plan pays for failed deploys and extra turns.
SERVICE_DEPS = {
    "db": [], "cache": [], "queue": [],
    "auth": ["db"], "api": ["db", "cache"],
    "web": ["api", "auth"], "worker": ["api", "queue"],
    "metrics": ["web", "worker"],
}

# rag_research: three quarterly figures buried in long documents. Chunks are
# deliberately big (~1k+ tokens) so every turn re-sends a heavy context —
# this is the payload-amplification scenario real RAG agents live in.
def _make_doc(title, quarter, revenue, seed_lines=55):
    lines = [f"# {title}", ""]
    for i in range(seed_lines):
        lines.append(
            f"Section {i+1}: Operational review item {i+1} covers routine matters "
            f"including staffing updates, facility maintenance schedules, vendor "
            f"contract renewals, compliance attestations, and inter-departmental "
            f"coordination notes logged during the reporting period for {title}.")
        if i == seed_lines // 2:
            lines.append(
                f"KEY FIGURE: Total recognized revenue for {quarter} was "
                f"${revenue} million, as confirmed by the finance controller.")
    return "\n".join(lines)

RAG_DOCS = {
    "q1_review": _make_doc("Q1 Operations Review", "Q1", 4.2),
    "q2_review": _make_doc("Q2 Operations Review", "Q2", 5.1),
    "q3_review": _make_doc("Q3 Operations Review", "Q3", 6.3),
    "hr_handbook": _make_doc("HR Handbook (no financials)", "N/A", 0.0).replace("KEY FIGURE:", "NOTE:"),
}
RAG_KEYWORDS = {"q1": "q1_review", "q2": "q2_review", "q3": "q3_review", "hr": "hr_handbook"}

# inventory_audit: aggregate discrepancies across 3 warehouses.
STOCK = {
    "W-EAST":  {"widget": 120, "gadget": 45, "sprocket": 200},
    "W-WEST":  {"widget": 80,  "gadget": 60},
    "W-NORTH": {"widget": 55,  "sprocket": 93, "gizmo": 14},
}
MANIFEST = {
    "W-EAST":  {"widget": 125, "gadget": 45, "sprocket": 200},   # missing 5
    "W-WEST":  {"widget": 80,  "gadget": 60},                    # missing 0
    "W-NORTH": {"widget": 60,  "sprocket": 95, "gizmo": 14},     # missing 5+2=7
}
# total missing across all warehouses = 12


def _err(msg):
    return json.dumps({"error": msg})


TASKS = [
    {
        "id": "order_totals",
        "goal": ("What is the combined total value of all orders placed by the "
                 "customer named 'Acme Corp'? Use the tools; when you know the "
                 "answer, reply in plain text with the number."),
        "tools": [
            tool("get_customer_id", "Look up a customer's id by exact name.",
                 {"name": {"type": "string"}}),
            tool("list_orders", "List order ids for a customer id.",
                 {"customer_id": {"type": "string"}}),
            tool("get_order_total", "Get the total value of one order id.",
                 {"order_id": {"type": "string"}}),
        ],
        "backend": {
            "get_customer_id": lambda a: json.dumps({"customer_id": CUSTOMERS[a["name"]]})
                if a["name"] in CUSTOMERS else _err("unknown customer"),
            "list_orders": lambda a: json.dumps({"orders": ORDERS.get(a["customer_id"], [])}),
            "get_order_total": lambda a: json.dumps({"total": ORDER_TOTALS[a["order_id"]]})
                if a["order_id"] in ORDER_TOTALS else _err("unknown order"),
        },
        "check": lambda text: "1630.49" in text.replace(",", ""),
        # optimal: get id -> list -> 3x totals -> answer = 5 tool turns min (parallel calls can shrink)
    },
    {
        "id": "warm_city",
        "goal": ("Check the weather in Berlin, Madrid, and Oslo, then book a "
                 "meeting room in whichever city is warmest. Use the tools; "
                 "after booking, confirm in plain text which city you booked."),
        "tools": [
            tool("get_weather", "Current weather for a city.",
                 {"city": {"type": "string"}}),
            tool("book_room", "Book a meeting room in a city. Returns a booking id.",
                 {"city": {"type": "string"}}),
        ],
        "backend": {
            "get_weather": lambda a: json.dumps(WEATHER.get(a["city"].lower().strip(), {"error": "unknown city"})),
            "book_room": lambda a: json.dumps({"booking_id": f"BK-{a['city'].lower().strip()}",
                                               "confirmed": True}),
        },
        "check": lambda text, state=None: "madrid" in text.lower(),
        "check_state": lambda calls: any(c["name"] == "book_room" and
                                         "madrid" in c["arguments"].lower() for c in calls),
    },
    {
        "id": "config_hunt",
        "goal": ("Find which file sets the deployment region, and report the "
                 "region value in plain text. Available files can be listed."),
        "tools": [
            tool("list_files", "List all file paths.", {}, []),
            tool("read_file", "Read a file's contents by path.",
                 {"path": {"type": "string"}}),
        ],
        "backend": {
            "list_files": lambda a: json.dumps({"files": sorted(FILES)}),
            "read_file": lambda a: json.dumps({"content": FILES[a["path"]]})
                if a["path"] in FILES else _err("no such file"),
        },
        "check": lambda text: "eu-central-1" in text,
    },
    {
        "id": "ticket_triage",
        "goal": ("Assign the highest-priority OPEN ticket to 'jordan', then "
                 "confirm in plain text which ticket id you assigned."),
        "tools": [
            tool("list_tickets", "List all ticket ids with status and priority.", {}, []),
            tool("assign_ticket", "Assign a ticket id to a person.",
                 {"ticket_id": {"type": "string"}, "assignee": {"type": "string"}}),
        ],
        "backend": {
            "list_tickets": lambda a: json.dumps({tid: {"status": t["status"], "priority": t["priority"]}
                                                  for tid, t in TICKETS.items()}),
            "assign_ticket": lambda a: json.dumps({"ok": True, "ticket": a["ticket_id"],
                                                   "assignee": a["assignee"]})
                if a["ticket_id"] in TICKETS else _err("unknown ticket"),
        },
        "check": lambda text: "t-2" in text.lower(),
        "check_state": lambda calls: any(c["name"] == "assign_ticket" and
                                         "T-2" in c["arguments"] for c in calls),
    },
    {
        "id": "multi_hop",
        "goal": ("Using the knowledge-base tool, find the population (in millions) "
                 "of the city where the agency that launched Voyager 1 is "
                 "headquartered. Answer in plain text."),
        "tools": [
            tool("kb_lookup", "Look up an entity; returns its known facts.",
                 {"entity": {"type": "string"}}),
        ],
        "backend": {
            "kb_lookup": lambda a: json.dumps(KG.get(a["entity"].strip(), {"error": "unknown entity — try the exact name"})),
        },
        "check": lambda text: "0.7" in text,
    },
    {
        "id": "flaky_tool",
        "goal": ("Fetch the invoice amount for invoice 'INV-9'. The fetch tool is "
                 "occasionally flaky — retry if it fails. Report the amount in "
                 "plain text."),
        "tools": [
            tool("fetch_invoice", "Fetch an invoice by id. May transiently fail.",
                 {"invoice_id": {"type": "string"}}),
        ],
        # first call fails with a retryable error, second succeeds (per-run state)
        "backend_factory": lambda: {
            "fetch_invoice": (lambda state: lambda a:
                _err("transient backend timeout — please retry")
                if state.update(n=state.get("n", 0) + 1) or state["n"] == 1
                else json.dumps({"invoice_id": "INV-9", "amount": 431.25}))({}),
        },
        "check": lambda text: "431.25" in text,
    },
]

# Hard suite: long-horizon and big-payload tasks where trajectory shape should
# actually differ between models. Run with --suite hard (or --suite all).

def _deploy_backend():
    state = {"deployed": set(), "failed_attempts": 0}
    def get_deps(a):
        svc = a["service"].strip()
        if svc not in SERVICE_DEPS:
            return _err(f"unknown service {svc}")
        return json.dumps({"service": svc, "depends_on": SERVICE_DEPS[svc]})
    def deploy(a):
        svc = a["service"].strip()
        if svc not in SERVICE_DEPS:
            return _err(f"unknown service {svc}")
        missing = [d for d in SERVICE_DEPS[svc] if d not in state["deployed"]]
        if missing:
            state["failed_attempts"] += 1
            return _err(f"cannot deploy {svc}: dependencies not deployed yet: {missing}")
        state["deployed"].add(svc)
        return json.dumps({"deployed": svc, "total_deployed": len(state["deployed"])})
    def status(a):
        return json.dumps({"deployed": sorted(state["deployed"])})
    return {"get_dependencies": get_deps, "deploy_service": deploy,
            "deployment_status": status}, state


HARD_TASKS = [
    {
        "id": "deploy_chain",
        "goal": ("Deploy the 'metrics' service. Services can only be deployed after "
                 "ALL their dependencies (transitively) are deployed. Use the tools to "
                 "discover dependencies and deploy everything needed in a valid order. "
                 "When 'metrics' is deployed, confirm in plain text how many services "
                 "you deployed in total."),
        "tools": [
            tool("get_dependencies", "List the direct dependencies of a service.",
                 {"service": {"type": "string"}}),
            tool("deploy_service", "Deploy one service. Fails unless all its dependencies are already deployed.",
                 {"service": {"type": "string"}}),
            tool("deployment_status", "List currently deployed services.", {}, []),
        ],
        "backend_factory": lambda: _deploy_backend()[0],
        "check": lambda text: bool(re.search(r"\b(8|eight)\b", text.lower())),
        "max_turns": 20,  # legitimate solutions need ~9 turns; wanderers need headroom
        # optimal: discover deps (1-3 turns) + deploy 8 services in order + answer.
        # A planner can batch parallel deploys; a wanderer hits dependency errors.
    },
    {
        "id": "rag_research",
        "goal": ("Using the document tools, find the total recognized revenue across "
                 "Q1, Q2, and Q3 (each quarterly review document states its figure), "
                 "then report the combined total in $ millions in plain text."),
        "tools": [
            tool("search_docs", "Search documents by keyword; returns matching doc ids.",
                 {"keyword": {"type": "string"}}),
            tool("read_doc", "Read the FULL text of a document by id (documents are long).",
                 {"doc_id": {"type": "string"}}),
        ],
        "backend": {
            "search_docs": lambda a: json.dumps({"matches": sorted({v for k, v in RAG_KEYWORDS.items()
                                                 if k in a["keyword"].lower()} or set(RAG_DOCS))}),
            "read_doc": lambda a: json.dumps({"doc_id": a["doc_id"], "content": RAG_DOCS[a["doc_id"]]})
                if a["doc_id"] in RAG_DOCS else _err("no such doc"),
        },
        "check": lambda text: "15.6" in text,
        # 4.2 + 5.1 + 6.3 = 15.6. Each read_doc returns ~1k+ tokens that get
        # re-sent on every later turn — the payload-amplification scenario.
    },
    {
        "id": "inventory_audit",
        "goal": ("Audit all warehouses: for every item, compare actual stock against "
                 "the shipping manifest, and report the TOTAL number of missing units "
                 "across all warehouses as a plain-text number."),
        "tools": [
            tool("list_warehouses", "List warehouse ids.", {}, []),
            tool("get_stock", "Actual stock levels for one warehouse.",
                 {"warehouse": {"type": "string"}}),
            tool("get_manifest", "Manifest (expected) levels for one warehouse.",
                 {"warehouse": {"type": "string"}}),
        ],
        "backend": {
            "list_warehouses": lambda a: json.dumps({"warehouses": sorted(STOCK)}),
            "get_stock": lambda a: json.dumps(STOCK.get(a["warehouse"].strip().upper(), {}))
                if a["warehouse"].strip().upper() in STOCK else _err("unknown warehouse"),
            "get_manifest": lambda a: json.dumps(MANIFEST.get(a["warehouse"].strip().upper(), {}))
                if a["warehouse"].strip().upper() in MANIFEST else _err("unknown warehouse"),
        },
        "check": lambda text: bool(re.search(r"\b(12|twelve)\b", text.lower())),
        # optimal: list + 3x(stock+manifest) + answer; parallel calls compress it.
    },
]

SUITES = {"core": TASKS, "hard": HARD_TASKS, "all": TASKS + HARD_TASKS}


# ──────────────────────────────────────────────────────────── agentic loop ──

def run_trajectory(client, backend_label, model, effort, task):
    backend = task["backend_factory"]() if "backend_factory" in task else task["backend"]
    history = [{"role": "user", "content": task["goal"]}]
    kwargs = {"reasoning": {"effort": effort}} if effort else {}
    max_turns = task.get("max_turns", MAX_TURNS)

    turns = 0
    input_tokens_per_turn = []
    tool_calls_made = []
    tot_in = tot_out = tot_reasoning = 0
    cost = 0.0
    t0 = time.perf_counter()
    final_text = None
    outcome = "no_answer"

    while turns < max_turns:
        turns += 1
        try:
            r = client.responses.create(
                model=model, input=history, tools=task["tools"],
                max_output_tokens=MAX_OUTPUT_TOKENS, **kwargs)
        except Exception as e:
            return {"outcome": "api_error", "error": capture_error(e), "turns": turns,
                    "success": False, "tool_calls": len(tool_calls_made),
                    "input_tokens": tot_in, "output_tokens": tot_out,
                    "reasoning_tokens": tot_reasoning, "cost_usd": round(cost, 6),
                    "input_tokens_per_turn": input_tokens_per_turn,
                    "wall_s": round(time.perf_counter() - t0, 2), "final_text": None}

        u = r.usage
        input_tokens_per_turn.append(u.input_tokens)
        tot_in += u.input_tokens
        tot_out += u.output_tokens
        tot_reasoning += getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0
        c = call_cost_usd(backend_label, model, u.input_tokens, u.output_tokens)
        cost += c or 0.0

        calls = [o for o in r.output if o.type == "function_call"]
        if not calls:
            final_text = r.output_text or ""
            outcome = "answered"
            break

        # extend history with the model's calls + our tool results
        for call in calls:
            tool_calls_made.append({"name": call.name, "arguments": call.arguments})
            history.append({"type": "function_call", "name": call.name,
                            "call_id": call.call_id, "arguments": call.arguments})
            fn = backend.get(call.name)
            if fn is None:
                result = _err(f"unknown tool {call.name}")
            else:
                try:
                    result = fn(json.loads(call.arguments or "{}"))
                except Exception as e:
                    result = _err(f"tool raised: {e}")
            history.append({"type": "function_call_output",
                            "call_id": call.call_id, "output": result})
    else:
        outcome = "max_turns"

    success = False
    if final_text is not None:
        try:
            success = bool(task["check"](final_text))
        except TypeError:
            success = bool(task["check"](final_text, None))
    if success and "check_state" in task:
        success = bool(task["check_state"](tool_calls_made))

    return {"outcome": outcome, "error": None, "turns": turns, "success": success,
            "tool_calls": len(tool_calls_made),
            "input_tokens": tot_in, "output_tokens": tot_out,
            "reasoning_tokens": tot_reasoning, "cost_usd": round(cost, 6),
            "input_tokens_per_turn": input_tokens_per_turn,
            "wall_s": round(time.perf_counter() - t0, 2),
            "final_text": (final_text or "")[:500]}


def main():
    p = argparse.ArgumentParser(description="Multi-turn agentic task evals")
    p.add_argument("--backend", choices=["mantle", "saas"], required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", help="reasoning effort (e.g. none); omit for default")
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--suite", choices=["core", "hard", "all"], default="core",
                   help="core = original 6 tasks; hard = long-horizon/big-payload; all = both")
    p.add_argument("--tasks", help="comma-separated task ids (default: whole suite)")
    args = p.parse_args()

    client, base_url = make_client(args.backend)
    pool = SUITES[args.suite]
    wanted = args.tasks.split(",") if args.tasks else None
    tasks = [t for t in pool if wanted is None or t["id"] in wanted]

    print(f"Agentic evals: {len(tasks)} tasks x {args.repeats} repeats | "
          f"{args.backend}/{args.model}" + (f" | effort={args.effort}" if args.effort else ""))

    all_results = []
    for task in tasks:
        for rep in range(args.repeats):
            r = run_trajectory(client, args.backend, args.model, args.effort, task)
            r["task"] = task["id"]
            r["repeat"] = rep
            all_results.append(r)
            print(f"  {task['id']:>13} #{rep+1}: {'OK ' if r['success'] else 'FAIL'} "
                  f"turns={r['turns']:>2} tools={r['tool_calls']:>2} "
                  f"in_tok={r['input_tokens']:>6} cost=${r['cost_usd']:.4f} "
                  f"({r['outcome']})")

    ok = [r for r in all_results if r["error"] is None]
    succ = [r for r in ok if r["success"]]
    total_cost = sum(r["cost_usd"] for r in ok)
    summary = {
        "n_runs": len(all_results),
        "n_errors": len(all_results) - len(ok),
        "n_success": len(succ),
        "success_rate": round(len(succ) / len(ok), 4) if ok else None,
        "mean_turns_success": round(sum(r["turns"] for r in succ) / len(succ), 2) if succ else None,
        "mean_turns_all": round(sum(r["turns"] for r in ok) / len(ok), 2) if ok else None,
        "mean_tool_calls": round(sum(r["tool_calls"] for r in ok) / len(ok), 2) if ok else None,
        "mean_input_tokens": round(sum(r["input_tokens"] for r in ok) / len(ok), 1) if ok else None,
        "mean_output_tokens": round(sum(r["output_tokens"] for r in ok) / len(ok), 1) if ok else None,
        "mean_wall_s": round(sum(r["wall_s"] for r in ok) / len(ok), 2) if ok else None,
        "total_cost_usd": round(total_cost, 6),
        "mean_cost_per_run_usd": round(total_cost / len(ok), 6) if ok else None,
        "cost_per_success_usd": round(total_cost / len(succ), 6) if succ else None,
        "per_task": {},
    }
    for task in tasks:
        tr = [r for r in ok if r["task"] == task["id"]]
        ts = [r for r in tr if r["success"]]
        # mean input tokens on the final turn / first turn: the context-growth
        # multiplier a customer pays for this task's trajectory length
        growth = [r["input_tokens_per_turn"][-1] / r["input_tokens_per_turn"][0]
                  for r in tr if r.get("input_tokens_per_turn") and r["input_tokens_per_turn"][0] > 0
                  and len(r["input_tokens_per_turn"]) > 1]
        summary["per_task"][task["id"]] = {
            "success": f"{len(ts)}/{len(tr)}",
            "mean_turns": round(sum(r["turns"] for r in tr) / len(tr), 2) if tr else None,
            "mean_cost_usd": round(sum(r["cost_usd"] for r in tr) / len(tr), 6) if tr else None,
            "mean_input_tokens": round(sum(r["input_tokens"] for r in tr) / len(tr), 1) if tr else None,
            "context_growth_x": round(sum(growth) / len(growth), 2) if growth else None,
        }

    print(f"\nSUMMARY: success {summary['n_success']}/{len(ok)} "
          f"| mean turns {summary['mean_turns_all']} "
          f"| mean cost/run ${summary['mean_cost_per_run_usd']} "
          f"| cost/success ${summary['cost_per_success_usd']}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_model = args.model.replace("/", "-")
    path = os.path.join(RESULTS_DIR,
        f"agentic_{args.suite}_{args.backend}_{safe_model}" + (f"_{args.effort}" if args.effort else "") + f"_{ts}.json")
    with open(path, "w") as f:
        json.dump({"backend": args.backend, "model": args.model, "base_url": base_url,
                   "reasoning_effort": args.effort, "max_turns": MAX_TURNS,
                   "suite": args.suite,
                   "repeats": args.repeats, "timestamp": ts,
                   "summary": summary, "results": all_results}, f, indent=2)
    print(f"Saved {os.path.basename(path)}")


if __name__ == "__main__":
    main()
