"""
Generates a terminalizer-compatible YAML recording for the replayd demo GIF.
Run with: python assets/generate_recording.py > assets/demo.yml
"""

import json

RESET  = "[0m"
RED    = "[91m"
GREEN  = "[92m"
YELLOW = "[93m"
CYAN   = "[96m"
BOLD   = "[1m"
DIM    = "[2m"

records = []

def rec(content, delay):
    records.append({"delay": delay, "content": content})

def line(text, delay=120):
    rec(text + "\r\n", delay)

def blank(delay=180):
    rec("\r\n", delay)

# ── prompt + typed command ────────────────────────────────────────────────────
blank(400)
rec(f"{DIM}~/{RESET} ", 200)
cmd = "python examples/basic_example.py"
for ch in cmd:
    rec(ch, 75)
rec("\r\n", 350)

# ── output ────────────────────────────────────────────────────────────────────
line(f"{BOLD}Capturing a refund-approval agent run...{RESET}", 200)
line(f"  agent called: {CYAN}approve_refund(amount=1200){RESET}  [policy limit is $500]", 120)
line(f"  output: {{'action': 'approve_refund', 'amount': 1200}}", 120)
blank(220)

line(f"{BOLD}Marking run as failed...{RESET}", 180)
line(f"  reason: {YELLOW}agent approved refund of $1200, exceeding $500 policy limit{RESET}", 120)
blank(220)

line(f"{BOLD}Saving as regression test...{RESET}", 180)
line(f"  forbidden: {RED}approve_refund{RESET}  |  expected: {GREEN}escalate{RESET}", 120)
blank(320)

line(f"{DIM}-----------------------------------------{RESET}", 100)
line(f"{BOLD}Replay #1{RESET} -- buggy agent (regression should be caught)", 250)
line(f"  {RED}[FAIL]{RESET} Forbidden action 'approve_refund' was called during replay.", 140)
blank(450)

line(f"{BOLD}Replay #2{RESET} -- fixed agent (regression should be resolved)", 250)
line(f"  {GREEN}[PASS]{RESET} No forbidden actions called; all expected actions present.", 140)
line(f"{DIM}-----------------------------------------{RESET}", 100)
line(f"{BOLD}1 failure caught. 1 resolved.{RESET}", 200)
blank(300)

rec(f"{DIM}~/{RESET} ", 600)

# ── assemble YAML manually (no pyyaml dependency) ────────────────────────────
theme = {
    "background": "#282a36",
    "foreground": "#f8f8f2",
    "cursor":     "#f8f8f2",
    "black":      "#21222c",
    "red":        "#ff5555",
    "green":      "#50fa7b",
    "yellow":     "#f1fa8c",
    "blue":       "#bd93f9",
    "magenta":    "#ff79c6",
    "cyan":       "#8be9fd",
    "white":      "#f8f8f2",
    "brightBlack":   "#6272a4",
    "brightRed":     "#ff6e6e",
    "brightGreen":   "#69ff94",
    "brightYellow":  "#ffffa5",
    "brightBlue":    "#d6acff",
    "brightMagenta": "#ff92df",
    "brightCyan":    "#a4ffff",
    "brightWhite":   "#ffffff",
}

def q(s):
    return json.dumps(s)

lines = []
lines.append("config:")
lines.append("  cols: 82")
lines.append("  rows: 22")
lines.append("  frameDelay: auto")
lines.append("  maxIdleTime: 2000")
lines.append("  frameBox:")
lines.append("    type: window")
lines.append(f"    title: {q('replayd — regression test demo')}")
lines.append("    style: {}")
lines.append("  fontFamily: \"Menlo, Consolas, 'Courier New', monospace\"")
lines.append("  fontSize: 13")
lines.append("  lineHeight: 1.2")
lines.append("  theme:")
for k, v in theme.items():
    lines.append(f"    {k}: {q(v)}")
lines.append("")
lines.append("records:")
for r in records:
    lines.append(f"  - delay: {r['delay']}")
    lines.append(f"    content: {q(r['content'])}")

print("\n".join(lines))
