from __future__ import annotations

from claude_runner.config import ROLES, Settings


def test_qa_agent_profile_has_read_only_tools() -> None:
    s = Settings.from_env({})
    # qa_agent is a runner role with its own profile.
    assert "qa_agent" in ROLES
    prof = s.profile("qa_agent")
    assert prof.permission_mode == "dontAsk"
    # The tool set is restricted to Read, Glob and Grep; nothing else is offered.
    assert prof.tools == "Read,Glob,Grep"
    # No write tools are allowed; they are denied explicitly.
    for tool in ("Edit", "Write", "NotebookEdit"):
        assert tool in prof.disallowed_tools
    # No allowed_tools (the --allowedTools flag is not set); the tools set does the work.
    assert prof.allowed_tools == []
    # The Worker and Reviewer profiles are unchanged (pinned).
    worker = s.profile("worker")
    assert worker.permission_mode == "bypassPermissions"
    assert worker.allowed_tools == []
    assert worker.disallowed_tools == Settings.from_env({}).profile("worker").disallowed_tools
    assert "Read(./.env)" in worker.disallowed_tools
    reviewer = s.profile("reviewer")
    assert reviewer.permission_mode == "dontAsk"
    assert "Bash(ahawr-search *)" in reviewer.allowed_tools
    assert "Edit" in reviewer.disallowed_tools
