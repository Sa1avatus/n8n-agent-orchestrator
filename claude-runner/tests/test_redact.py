import pytest

from claude_runner.redact import redact

MASKED_CASES = [
    (
        "postgresql://appuser:fake-pass-0001@db.example.test/app",
        "postgresql://appuser:***@db.example.test/app",
    ),
    (
        "dsn postgres://svc:fake-pass-0002@localhost:5432/demo ok",
        "dsn postgres://svc:***@localhost:5432/demo ok",
    ),
    (
        "Authorization: Bearer fake.Token-Value_0123456789",
        "Authorization: Bearer ***",
    ),
    ("key sk-fakeexamplekey0123456789 in use", "key *** in use"),
    ("OPENAI_API_KEY=fake-key-0003 started", "OPENAI_API_KEY=*** started"),
    ("DATABASE_PASSWORD=fakeHunter2 started", "DATABASE_PASSWORD=*** started"),
    ('TELEGRAM_BOT_TOKEN="fake-token-0004" set', "TELEGRAM_BOT_TOKEN=*** set"),
    ("SECRET=fake-secret-0005", "SECRET=***"),
    ("token 0123456789abcdef0123456789abcdef", "token ***"),
    (
        "secret: ZmFrZS1zZWNyZXQtdmFsdWUtMDAwNS1tYWRlLXVwLW9ubHk=",
        "secret: ***",
    ),
]

UNCHANGED_CASES = [
    "Normal prose stays as it is, with no secrets at all.",
    "Bearer of bad news is still a sentence.",
    "commit 9fceb02d0ae598e95dc970b74767f19372d61af8 Fix key handling in the parser",
    "postgresql://db.example.test:5432/app",
    "see https://example.test/docs?page=2 for details",
    "password = get_password(config)",
    "api_key = load_key(path)",
]


@pytest.mark.parametrize(("text", "expected"), MASKED_CASES)
def test_redact_masks_secret_and_keeps_context(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.mark.parametrize("text", UNCHANGED_CASES)
def test_redact_returns_ordinary_text_unchanged(text: str) -> None:
    assert redact(text) == text


def test_redact_empty_string() -> None:
    assert redact("") == ""
