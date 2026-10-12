# T003 Review: docs/telegram/hermes-commands.md vs the Hermes registry

## What I checked

- Read `docs/telegram/hermes-commands.md` in full (62 lines).
- Verified against the registry copy at `docs/telegram/reference/hermes_commands_registry.py.txt` (746 lines, byte-identical to `hermes_cli/commands.py`). The original `D:/Hermes/hermes-agent/hermes_cli/commands.py` is **not reachable** from the Worker/Reviewer sandbox, so every citation below points at the reference copy.

## Command total and per-category totals

Parsing `COMMAND_REGISTRY` (reference file lines 49–307) gives:

| Category | Count |
|---|---|
| Session | 42 |
| Configuration | 21 |
| Info | 19 |
| Tools & Skills | 19 |
| Exit | 1 |
| **Total** | **102** |

This matches the doc's stated "102 commands" and its per-category breakdown.

## The adopt / adapt / add / defer split

| Split | Commands | Registry check |
|---|---|---|
| **adopt** (4) | `/start`, `/help`, `/commands`, `/whoami` | All present in registry |
| **adapt** (20) | `/status`, `/stop`, `/pause`, `/resume`, `/restart`, `/retry`, `/undo`, `/approve`, `/deny`, `/config`, `/model`, `/agents`, `/usage`, `/insights`, `/profile`, `/version`, `/sethome`, `/queue`, `/steer`, `/debug` | All present in registry (note: `/debug` is Hermes' "upload debug report"; AHAWR repurposes it as `/errors`) |
| **add** (7) | `/missions`, `/mission`, `/progress`, `/start <mission>`, `/accept`, `/set`, `/audit` | **Not in registry** — these are AHAWR-only. Correct: none of these exist as `CommandDef` entries |
| **defer** (2) | `/tools`, `/cron` | Both present in registry, both `cli_only=True` |

Total used = 4 + 20 + 7 + 2 = **33** Hermes-sourced + 7 AHAWR-added. The doc's "76 unused" is the complement: 102 − 26 = 76, where 26 = the 4 adopt + 20 adapt + 2 defer that are *in the registry*. (The 7 added commands are not counted against the registry because they have no Hermes equivalent.)

## Scope (CLI vs chat) verification

Every "Scope in Hermes" cell in the doc matches the registry flags:

| Command | Doc says | Registry |
|---|---|---|
| `/start` | chat only | `gateway_only=True` ✓ |
| `/help` | CLI and chat | neither flag ✓ |
| `/commands` | chat only | `gateway_only=True` ✓ |
| `/whoami` | CLI and chat | neither flag ✓ |
| `/status` | CLI and chat | neither flag ✓ |
| `/stop` | CLI and chat | neither flag ✓ |
| `/pause` | chat only | `gateway_only=True` ✓ |
| `/resume` | CLI and chat | neither flag ✓ |
| `/restart` | chat only | `gateway_only=True` ✓ |
| `/retry` | CLI and chat | neither flag ✓ |
| `/undo` | CLI and chat | neither flag ✓ |
| `/approve` | chat only | `gateway_only=True` ✓ |
| `/deny` | chat only | `gateway_only=True` ✓ |
| `/config` | CLI only | `cli_only=True` ✓ |
| `/model` | CLI and chat | neither flag ✓ |
| `/agents` | CLI and chat | neither flag ✓ (alias `tasks`) |
| `/usage` | CLI and chat | neither flag ✓ |
| `/insights` | CLI and chat | neither flag ✓ |
| `/profile` | CLI and chat | neither flag ✓ |
| `/version` | CLI and chat | neither flag ✓ (alias `v`) |
| `/sethome` | chat only | `gateway_only=True` ✓ (alias `set-home`) |
| `/queue` | CLI and chat | neither flag ✓ (alias `q`) |
| `/steer` | CLI and chat | neither flag ✓ |
| `/debug` | CLI and chat | neither flag ✓ |
| `/tools` | CLI only | `cli_only=True` ✓ |
| `/cron` | CLI only | `cli_only=True` ✓ |

## The 76 unused, per category

The doc lists the 76 unused by category. I verified that `used (per category) + unused (per category) == registry (per category)` for every category:

- Session: 14 used + 28 unused = 42 ✓
- Configuration: 2 used + 19 unused = 21 ✓
- Info: 8 used + 11 unused = 19 ✓
- Tools & Skills: 2 used + 17 unused = 19 ✓
- Exit: 0 used + 1 unused = 1 ✓

Every command named in the doc's "Not used" list exists in the registry, and no registry command is missing from either the used or unused list.

## Conventions in the doc — verification

| Doc claim | Where in the reference file | Verdict |
|---|---|---|
| Canonical name, aliases, argument hint, category | `CommandDef` dataclass, lines 20–43 | ✓ matches |
| `/help` grouped by category, `/commands [page]` paginated | `help` line 273–274 (`execute="gateway_help"`), `commands` line 270–272 (`execute="gateway_commands"`, `args_hint="[page]"`) | ✓ |
| `setMyCommands` registration, two-bot/two-token constraint | Doc line 7; lazy module pointer line 732–734 (`telegram_bot_commands`, `telegram_menu_commands`) | ✓ consistent (full impl in `commands_platforms`) |
| Telegram names `a-z 0-9 _`, up to 32 chars | `_CMD_NAME_LIMIT = 32` line 513; `_clamp_command_names` lines 515–551 | ✓ |
| `reload-mcp` offered as `/reload_mcp` | Line 473 treats `-` and `_` as equivalent for dedup; `reload-mcp` alias `reload_mcp` line 260 | ✓ |

## Corrections needed

**None.** The doc is accurate in every verifiable claim:
- 102 total commands, correct per-category totals (42/21/19/19/1).
- The adopt/adapt/add/defer split is correctly assigned.
- Every "Scope in Hermes" cell matches the registry's `cli_only`/`gateway_only` flags.
- The 76 unused list is complete and its per-category partition matches the registry exactly.
- The two "add" commands' "later" deferral (`/tools`, `/cron`) is correctly flagged, and both are indeed `cli_only` in the registry.

The only nuance worth noting (not a correction): the doc's 26 "used" count excludes the 7 AHAWR-added commands, which have no registry entry — that is by design, not an error.
