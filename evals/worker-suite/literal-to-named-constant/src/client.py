def request_options(retries: int) -> dict[str, int]:
    return {"timeout": 30, "retries": retries}
