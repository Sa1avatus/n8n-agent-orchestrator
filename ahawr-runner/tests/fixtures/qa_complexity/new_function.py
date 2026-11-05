def search_words(words: list[str], target: str) -> list[str]:
    matches = []
    for word in words:
        if word == target:
            matches.append(word)
    return matches
