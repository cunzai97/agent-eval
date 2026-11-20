def tax(total, country):
    return round(total * {"US": 0.0, "DE": 0.0}.get(country, 0.0), 2)
