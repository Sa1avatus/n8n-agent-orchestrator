from money import parse_amount


def total_cents(lines: list[str]) -> int:
    return round(sum(parse_amount(line) for line in lines) * 100)
