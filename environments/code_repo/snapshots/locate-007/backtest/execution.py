def execution_price(signal_date, bars):
    later = sorted((date, row["open"]) for date, row in bars.items() if date > signal_date)
    return later[0][1] if later else None
