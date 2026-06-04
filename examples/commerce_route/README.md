# Commerce Route Replay Demo

This demo shows a failed AI agent run becoming a replayable regression test.
The case is small on purpose so it works well in a PR check and in a short
recording.

## What It Shows

A commerce agent receives this user input:

```text
Can I see a picture?
```

The expected route is:

```text
send_product_image
```

The broken agent returns:

```text
text_only
```

That is wrong because the user asked to see a picture. A text-only response
does not satisfy the request and skips the product-image route the application
needs to call.

## From Failure To Replay Case

`capture_failure.py` runs the broken agent, compares the actual route with the
expected route, and writes the failed run as a JSON replay case:

```text
examples/commerce_route/.replayd/tests/case_07_send_product_image.json
```

That fixture becomes the PR-native release gate. Future agent versions replay
the same input and must return `send_product_image`.

## Run The Broken Check

```bash
python examples/commerce_route/run_replay.py --agent broken
```

Expected result:

```text
decision:
BLOCK

exit code:
1
```

The command exits with code `1` because the route is still `text_only`.

## Run The Fixed Check

```bash
python examples/commerce_route/run_replay.py --agent fixed
```

Expected result:

```text
decision:
PASS

exit code:
0
```

The command exits with code `0` because the fixed agent returns
`send_product_image`.

## CI And TAQ Release Gates

In CI, the check should run the fixed production candidate:

```bash
python examples/commerce_route/run_replay.py --agent fixed
```

If a prompt change, model change, or route change brings back the old
`text_only` behavior, the replay command exits `1` and the PR check blocks the
release. In TAQ terms, the old agent failure should not reach users twice.

For a red-check demo branch, intentionally run:

```bash
python examples/commerce_route/run_replay.py --agent broken
```

That shows the same replay case blocking the broken behavior without adding an
active workflow that fails the main branch by default.
