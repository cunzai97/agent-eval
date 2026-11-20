from .parse import parse_numbers


def summarize(text):
    values = parse_numbers(text)
    if not values:
        return {"count": 0, "total": "0.00", "mean": "0.00", "median": "0.00"}
    return {
        "count": len(values),
        "total": f"{sum(values):.2f}",
        "mean": f"{sum(values) / len(values):.2f}",
        "median": f"{sorted(values)[len(values) // 2]:.2f}",
    }
