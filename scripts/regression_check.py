"""
Drop this file into your repo and call it from CI to enforce regression tests.

Usage:
    python scripts/regression_check.py

Replace `your_agent_fn` with your actual agent wrapped as:
    def your_agent_fn(input, run_ctx):
        result = your_agent.run(input)
        run_ctx.record_tool_call("tool_name", {...}, result)
        return result

Add to your GitHub Actions workflow:
    - name: Run regression tests
      run: python scripts/regression_check.py
"""

import sys
from replayd import Replayd

# Replace this import with your actual agent
# from your_agent import your_agent_fn
def your_agent_fn(input, run_ctx):
    raise NotImplementedError("Replace this with your real agent")

rp = Replayd()
results = rp.replay_all(agent=your_agent_fn)

if not results:
    print("No regression tests saved yet. Use rp.save_test() to add some.")
    sys.exit(0)

failures = [r for r in results if not r]
passes = [r for r in results if r]

for r in passes:
    print(f"PASS  {r.test.failure_reason}")
for r in failures:
    print(f"FAIL  {r.test.failure_reason}: {r.reason}")

print(f"\n{len(passes)} passed, {len(failures)} failed out of {len(results)} tests.")

if failures:
    sys.exit(1)
