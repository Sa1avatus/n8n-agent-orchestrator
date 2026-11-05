def outer(flag: bool) -> int:
    try:
        if flag:
            while True:
                return 1
    except ValueError:
        def inner() -> int:
            return 2
        return inner()
    return 0
