from .rules import scale


def to_minor_units(value, currency="USD"):
    text = str(value).strip().replace("$", "").replace(",", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    result = int(float(text) * scale(currency))
    return -result if negative else result
