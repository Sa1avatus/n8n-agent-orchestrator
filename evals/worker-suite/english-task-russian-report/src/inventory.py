def add_item(stock: dict[str, int], name: str, qty: int) -> None:
    stock[name] = stock.get(name, 0) + qty


def remove_item(stock: dict[str, int], name: str, qty: int) -> None:
    stock[name] = stock.get(name, 0) - qty
