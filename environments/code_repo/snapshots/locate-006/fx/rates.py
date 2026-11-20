RATES = {"USD": 1.0, "EUR": 0.8, "CNY": 7.2}


def convert_usd(amount, currency):
    return round(amount * RATES[currency], 2)
