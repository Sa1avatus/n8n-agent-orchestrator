def count_marks(items: list[str]) -> int:
    """Count the non-blank lines of the list."""
    total = 0
    for line in items:
        if line:
            total += 1
    return total
