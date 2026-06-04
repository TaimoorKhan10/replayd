# CI Demo: replayd / commerce route

This is a safe GitHub Actions example for the commerce route demo. It is kept
as documentation so it does not change the repository's active CI behavior.

```yaml
name: replayd / commerce route

on:
  pull_request:

jobs:
  commerce-route:
    name: replayd / commerce route
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Run replayd commerce route check
        run: python examples/commerce_route/run_replay.py --agent fixed
```

To record a red PR check on a demo branch, change the command to:

```bash
python examples/commerce_route/run_replay.py --agent broken
```

The broken command exits `1`, so GitHub marks the check as failed. The fixed
command exits `0`, so the check passes.
