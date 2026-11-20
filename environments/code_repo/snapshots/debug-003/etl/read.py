DB = {
    "ok": [
        {"value": "1", "active": "true"},
        {"value": "2", "active": "false"},
        {"value": "bad", "active": "true"},
    ]
}


def read(path):
    return DB.get(path)
