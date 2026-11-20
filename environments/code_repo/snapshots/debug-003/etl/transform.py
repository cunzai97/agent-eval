from .read import read


def run(path):
    rows = read(path)
    output = []
    for row in rows:
        if row.get("active"):
            row["value"] = int(row["value"]) * 2
            output.append(row["value"])
    return output
