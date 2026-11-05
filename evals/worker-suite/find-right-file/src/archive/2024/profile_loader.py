RETRY_LIMIT = 3


def load_profile_v1(name: str) -> dict[str, str]:
    return {"name": name, "version": "1"}
