"""Shared, case-sensitive MRU history for executed query input fields."""
MAX_RECENT_SEARCH_TERMS = 50


def normalize_search_terms(values) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        term = value.strip()
        if term and term not in seen:
            seen.add(term)
            result.append(term)
            if len(result) == MAX_RECENT_SEARCH_TERMS:
                break
    return result


def remember_search_terms(previous, used) -> list[str]:
    # Preserve field order within one query, move the whole used group to the front.
    return normalize_search_terms(normalize_search_terms(used) + normalize_search_terms(previous))
