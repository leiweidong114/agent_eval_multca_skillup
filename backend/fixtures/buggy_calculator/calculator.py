def median(values):
    """Return the median of a non-empty sequence of numbers."""
    if not values:
        return 0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return ordered[midpoint]
