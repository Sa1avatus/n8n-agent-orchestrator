MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2


def run_with_retry(call):
    last_error = None
    for _ in range(MAX_ATTEMPTS):
        try:
            return call()
        except OSError as error:
            last_error = error
    raise last_error
