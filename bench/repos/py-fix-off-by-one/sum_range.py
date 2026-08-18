def sum_range(low, high):
    """Return the sum of the integers from low to high, INCLUSIVE."""
    total = 0
    for i in range(low, high):  # BUG: excludes `high`
        total += i
    return total
