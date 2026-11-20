def parse_numbers(text):
    return [float(item.strip()) for item in text.split(",") if item.strip()]
