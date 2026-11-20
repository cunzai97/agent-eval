def add_tax(amount, country):
    rate = {"US": 0.0, "DE": 0.0}.get(country, 0.0)
    return round(amount * (1 + rate), 2)
