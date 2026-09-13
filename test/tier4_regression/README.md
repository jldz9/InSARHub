# Tier 4 — bug regressions

One file per previously-fixed bug. The point is narrow: **prove that this
specific bug is still fixed**, in this version and every future one.

These are deliberately *not* general contract tests. A contract test says what
the system should do; a regression test reproduces the exact input that once
produced the wrong answer. Both are worth having, and they live in different
tiers so that a broad refactor of tier 2 can never quietly delete the only proof
that a shipped bug stayed fixed.

## Adding one

When you fix a bug, add a file here before you fix it — watch it fail, then make
it pass.

```
test/tier4_regression/test_bug_<version>_<slug>.py
```

Every file starts with the same header so the history stays readable without
digging through git:

```python
"""<one-line symptom, as a user would describe it>

Fixed in:   0.4.0
Changelog:  "Fixed CLI --stacks PATH:FRAME selecting nothing for S1_Burst"
Symptom:    <what the user saw>
Root cause: <why it happened>
"""

BUG = {
    "id": "0400-burst-stack-selection",
    "fixed_in": "0.4.0",
    "area": "cli/downloader",
}
```

`BUG` is read by `test_bug_index.py`, which checks that every file here declares
one and that no two share an id.

## Rules

- **Reproduce, don't paraphrase.** Use the values from the real report — the
  actual burst ID, the actual malformed response. A simplified input often
  misses the thing that broke.
- **One bug per file.** Related assertions belong together; unrelated ones don't.
- **No network, no backends.** If a bug can only be reproduced against real
  ISCE2/GMTSAR, write the case in tier 3 and cross-reference it here.
- **Never delete a passing case** to make a refactor easier. If the behaviour
  genuinely changed on purpose, replace the assertion and say so in the
  docstring.

## Bugs that became standing contracts

A few fixes are better expressed as a permanent invariant than as a one-off
reproduction — the CORS lockdown, for example, is checked in
`tier2_basic/test_api_contract.py` because "the API is never readable
cross-origin" is a rule that must hold forever, not a single input to replay.
Those are listed in `test_bug_index.py` under `ELSEWHERE` so the bug is still
tracked from here.

## Currently-known, *unfixed* bugs

Bugs that are known but not yet fixed are recorded as `xfail(strict=True)` in
whichever tier owns the behaviour, not here. `strict=True` means the test fails
if the bug ever starts passing, so the xfail cannot outlive the bug. As of
0.4.0rc1:

| Bug | Recorded in |
|---|---|
| `get_config()` raises — `tomllib` used but never imported (`utils/tool.py:1677`) | `tier1_install/test_install.py` |
| `insarhub --verbose <cmd>` silently yields `verbose=0` | `tier2_basic/test_cli_contract.py` |
