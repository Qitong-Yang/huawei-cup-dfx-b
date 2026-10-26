"""Stand-in for the ``claude`` CLI in backend tests.

Reads a JSON script from the file named by ``FAKE_CLAUDE_SCRIPT``:
``{"answer": dict|null, "events": [...], "raw_tail": str, "sleep": float, "exit": int,
"argv_out": path}``. The answer is written to the ``--answer`` path found in ``--mcp-config``
before any event is printed (or at a ``__write_answer__`` marker), as if the model had called
submit.
"""

import json
import os
import sys
import time
from pathlib import Path

script = json.loads(Path(os.environ["FAKE_CLAUDE_SCRIPT"]).read_text(encoding="utf-8"))
argv = sys.argv[1:]
if script.get("argv_out"):
    with open(script["argv_out"], "w", encoding="utf-8") as f:
        json.dump(argv, f)
mcp = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
server_args = mcp["mcpServers"]["dfx"]["args"]
answer_path = server_args[server_args.index("--answer") + 1]
events = script.get("events", [])


def write_answer() -> None:
    with open(answer_path, "w", encoding="utf-8") as f:
        json.dump(script["answer"], f)


# Markers inside "events": {"__sleep__": s} pauses, {"__write_answer__": true} writes the answer
# at that point (as the submit tool would); without that marker the answer is written first.
if script.get("answer") is not None and not any("__write_answer__" in e for e in events):
    write_answer()
for event in events:
    if "__sleep__" in event:
        time.sleep(event["__sleep__"])
    elif "__write_answer__" in event:
        write_answer()
    else:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()
if script.get("raw_tail"):
    sys.stdout.write(script["raw_tail"])
    sys.stdout.flush()
time.sleep(script.get("sleep", 0))
sys.stderr.write(script.get("stderr", ""))
sys.exit(script.get("exit", 0))
