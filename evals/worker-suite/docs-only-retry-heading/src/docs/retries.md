# Retries

The retry policy lives in `src/retry.py`.

## Backoff

Each wait doubles from the previous one, starting at 2 seconds.
