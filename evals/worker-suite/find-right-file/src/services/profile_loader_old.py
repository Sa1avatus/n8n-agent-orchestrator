RETRY_LIMIT = 3


def load_profile_legacy(name: str) -> dict[str, str]:
    for _attempt in range(RETRY_LIMIT):
        if name:
            return {"name": name}
    raise RuntimeError("legacy loader gave up")
