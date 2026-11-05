OK_MAX_MS = 200


def classify(latency_ms: int) -> str:
    if latency_ms < OK_MAX_MS:
        return "ok"
    return "slow"
