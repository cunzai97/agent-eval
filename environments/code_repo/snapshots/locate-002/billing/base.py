def subtotal(lines):
    return sum(line["price"] * line.get("qty", 1) for line in lines)
