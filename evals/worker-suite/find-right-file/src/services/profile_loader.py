RETRY_LIMIT = 3


def load_profile(name: str) -> dict[str, str]:
    last_error: Exception | None = None
    for _attempt in range(RETRY_LIMIT):
        try:
            return _read_profile(name)
        except OSError as exc:
            last_error = exc
    raise RuntimeError(f"profile {name!r} unavailable") from last_error


def _read_profile(name: str) -> dict[str, str]:
    if not name:
        raise OSError("empty profile name")
    return {"name": name}
