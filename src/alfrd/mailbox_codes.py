"""Mailbox v2 type codes, shared by the store and its presentation layers.

REP is store-owned and inherits category and priority from the original mail.
The order here is the order of the frozen release contract's code table.
"""

PRIORITIES = ("low", "normal", "high", "urgent")
CATEGORIES = ("progress", "result", "question", "permission", "error", "blocker", "coordination", "note")

CODES = {
    "PRG": {"kind": "info", "category": "progress", "priority": "low", "meaning": "Step progress {step, of, label}"},
    "STR": {"kind": "info", "category": "progress", "priority": "low", "meaning": "Work started"},
    "DON": {"kind": "info", "category": "result", "priority": "normal", "meaning": "Main work finished"},
    "ART": {"kind": "info", "category": "result", "priority": "normal", "meaning": "Output file ready {path}"},
    "NTE": {"kind": "info", "category": "note", "priority": "low", "meaning": "FYI"},
    "WRN": {"kind": "info", "category": "error", "priority": "high", "meaning": "Degraded, continuing"},
    "ERR": {"kind": "info", "category": "error", "priority": "high", "meaning": "A step failed {cause?}"},
    "BLK": {"kind": "info", "category": "blocker", "priority": "urgent", "meaning": "Cannot proceed {needs?}"},
    "DEP": {"kind": "info", "category": "coordination", "priority": "normal", "meaning": "Note to a sibling or dependent task"},
    "CLM": {"kind": "info", "category": "coordination", "priority": "high", "meaning": "Claims a shared path {path}"},
    "ASK": {"kind": "question", "category": "question", "priority": "high", "meaning": "Question for the user"},
    "ASO": {"kind": "question", "category": "question", "priority": "high", "meaning": "Question for the orchestrator"},
    "PRM": {"kind": "permission_request", "category": "permission", "priority": "urgent", "meaning": "Approval for an exact action"},
    "REP": {"kind": "reply", "category": None, "priority": None, "meaning": "Set by the store"},
}


def defaults(message, original=None):
    """Project display defaults without modifying an authoritative record.

Uncoded info defaults to NTE; a supplied category selects its first code.
Legacy question codes follow the recipient, and reply defaults follow the parent.
"""
    kind = message["kind"]
    code = message.get("code")
    if code is None:
        if kind == "info":
            code = next((c for c, row in CODES.items() if row["kind"] == kind and
                         row["category"] == message.get("category", "note")), "NTE")
        else:
            code = {"question": "ASO" if message["recipient"]["kind"] == "orchestrator" else "ASK",
                    "permission_request": "PRM", "reply": "REP"}[kind]
    row = CODES[code]
    inherited = defaults(original) if kind == "reply" and original is not None else {}
    return {"code": code,
            "category": message.get("category", inherited.get("category", row["category"] or "note")),
            "priority": message.get("priority", inherited.get("priority", row["priority"] or "normal")),
            "summary": message.get("summary", message["subject"]),
            "description": message.get("description", ""),
            "step_id": message.get("step_id")}
