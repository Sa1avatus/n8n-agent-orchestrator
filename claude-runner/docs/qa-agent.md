# QA agent: design

Status: design. The next tasks implement the modules named below behind the flag `qa_agent`
in the QA settings, OFF by default. With the flag off every QA request, verdict and prompt
is byte-for-byte what it was before.

## Why

QA today is scripted: the layers `regression`, `api` and `ui` (see [qa-layers.md](qa-layers.md))
check what the mission's own tests and the throwaway stack assert. No model looks at the
result: the operator agreed one UI (a mockup variant), the Worker delivered another, and every
check was green because the checks only encode what the mission's own tests assert. The QA
agent adds a separate thinking-model role that checks a mission's UI against an agreed design
contract and shows up in the dashboard Runs with its events.

## The design contract

For each mission with a UI there is a contract:

```
claude-runner/qa/contracts/<mission_id>/contract.md
```

plus the agreed reference images (png) next to it. It is written when the operator picks a
design.

Format:

- a title;
- one section per screen with the route or the way to open it, the viewport, the layout in
  plain words, and the reference image file;
- numbered items C1, C2, ... each one checkable by looking at a screenshot or at an API
  answer (what must be visible, where, which texts; what must NOT be there).

Example of one screen section:

```markdown
# Board: providers

## Providers page
- Open: route /providers, viewport 1280x800
- Layout: cards in a row, each with Connection, API key and Models; a Delete provider
  button in the header; a Fetch models line with a name filter and a Free only switch
- Reference image: providers-reference.png
- C1: every provider card shows its connection name and its key's first characters
- C2: the header has exactly one Delete provider button per card
- C3: the list shows no rows while there is no provider in the database
```

The validator `qa_contract.py` parses the contract and checks the format: the title, one
section per screen with its route-or-opening, viewport, layout and reference image, and the
numbered items C1..Cn in order. It also checks that every named image file exists next to the
contract. A contract with a missing image file, a duplicate item number, or an empty item is
rejected; a mission whose folder has no contract is not a format error — the agent simply does
not run (see the rule at the end).

The two planned example contracts, written as the first test of the format:
`claude-runner/qa/contracts/ahawr-providers-ui/` (the Providers page) and
`claude-runner/qa/contracts/ahawr-guards-detail/` (the Guards page).

## The role `qa_agent`

A new role `qa_agent` in `roles_json`, separate from the scripted `qa` role. It is a run of the
runner like Worker and Reviewer: role `qa_agent`, its own provider and model from `roles_json`,
thinking ON through the existing role option `think` (there is no option named `thinking`; the
option key is `think`, `roles_config.py:16`), and the bounds `max_minutes` and `max_cost_usd`
from the role options (same option keys as the `qa` role, `roles_config.py:17`).

The role gets read-only tools only: Read, Glob, Grep. It reads PNG files with Read; it never
writes a file and never runs a shell command. In the role profile terms
(`config.py:135-147`), that is a `dontAsk` profile with no allowed tools and the read-only and
secret deny lists, the tools set restricted to Read, Glob and Grep.

Default model in the documented example: a thinking model (`anthropic/claude-opus-5-5` in the
operator example below; the operator sets the real one).

The role is defined in both implementations, `roles_config.py` and `board_roles.js` (the JS
core mirrors the Python one and both are tested against the shared `tests/roles_cases.json`,
see `roles-config.md`), with validation cases for the new role in the cases file.

## Capture

The command:

```
python -m claude_runner.qa capture --contract <mission_id>
```

It runs inside the QA container like the other `qa` subcommands (a new `capture`
subcommand of `python -m claude_runner.qa`, next to `run`, `api`, `smoke`, `regression`
and `baseline`). It:

1. starts the throwaway stack with the existing `qa_stack` (docker, inprocess or postgres
   mode; base URL `http://127.0.0.1:<port>` in every mode);
2. opens every screen of the contract with Playwright — the route, the viewport, and the
   steps to reach the state;
3. saves for each screen a png and a text dump of the visible DOM into the QA run folder
   (`qa-<8 hex chars>` under the QA runs directory, the same run-folder layout the smoke
   crawl and the scenario runs use).

The agent gets the file paths, not a browser. The capture is deterministic and has no model.

## The agent run and its answer

The worker builds the prompt and starts the run through `POST /v1/runs` with role `qa_agent`
(the existing run request carries `role`, `model`, `provider` and the prompt text). The prompt
contains the contract text, the list of screenshots with their screen names, the reference
images, and the answer schema.

The agent compares each screenshot with the reference image and with each item C1..Cn, and
ends with ONE JSON array:

```json
[{"item": "C3", "verdict": "pass|fail|blocked", "evidence": "<screenshot file name or API line>", "reason": "<one sentence>"}]
```

The parser validates the answer strictly:

- every item of the contract appears exactly once — a missing item and a duplicate item fail;
- `verdict` is one of `pass`, `fail`, `blocked`;
- `evidence` is not empty for `pass` and `fail` — an answer without evidence fails;
- any malformed answer (not the schema, not parseable JSON) counts as `fail` with a clear
  reason — no false green;
- `blocked` (the agent could not see the item) fails the gate too, with the reason shown.

## The modules

| Module | What it does |
| --- | --- |
| `qa_contract.py` | parses and validates a contract: screens, items C1..Cn, existing image files |
| `qa_capture.py` | builds the capture plan from a contract (route, viewport, steps), starts the stack, writes a png and a DOM text dump per screen; no model |
| `qa_agent_prompt.py` | builds the prompt: contract text, screenshots with their screen names, reference images, answer schema |
| `qa_agent_verdicts.py` | parses the agent's JSON answer strictly; a missing, duplicate, unevidenced or malformed answer fails with a clear reason |
| `qa_agent_run.py` | starts the run through the runner API with role `qa_agent` and turns the parsed answer into the request's verdicts |

## Integration

- The QA request gets the layer `agent`. The worker runs capture, then the agent, merges the
  agent's verdicts into the request result, and the mission's QA gate fails on any `fail` or
  `blocked` verdict. The `agent` layer joins the existing `regression`, `api` and `ui` layers
  (`QA_LAYERS`, `roles_config.py:21-22`) in both implementations.
- The agent run is a runner run of role `qa_agent` and is visible in the dashboard Runs with
  its events, like the other roles.

## The flag

The whole feature sits behind the flag `qa_agent` in the QA settings, OFF by default. The flag
is read from the `qa` options of `roles_json` in `hermes_config` the same way the `smoke`
flag is read: `options.get("qa_agent") is True`, and the options reader returns `{}` when the
config is missing or unreadable — that is how the flag defaults to OFF (see
[qa-gate.md](qa-gate.md) and `roles-config.md`). With the flag off the scripted QA is untouched:
every request, verdict and prompt is byte-for-byte what it was; a test shows that.

Operator steps to turn the feature on:

1. Add the role to `roles_json`, with a thinking model, thinking on, and the bounds:

   ```json
   {"qa_agent": {"1": "anthropic/claude-opus-5-5", "options": {"think": true, "max_minutes": 20, "max_cost_usd": 1}}}
   ```

2. Write the mission's contract at `claude-runner/qa/contracts/<mission_id>/contract.md` with
   its reference images (format above).
3. Set the flag `qa_agent` to `true` in the `qa` options of `roles_json`.
4. Request the layer as `agent` in the QA request; the worker then runs capture and the agent
   and merges their verdicts into the result.

## No contract, no agent run

A mission without a contract does not run the agent: the `qa_contract` validator has nothing to
validate, the capture has no screens, and the request proceeds with the scripted layers only.
Scripted QA stays exactly as it is.
