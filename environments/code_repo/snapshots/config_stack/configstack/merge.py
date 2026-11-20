"""Recursive merge for layered configuration mappings."""


def deep_merge(layers):
    result = {}
    for layer in layers:
        if not isinstance(layer, dict):
            raise TypeError("each layer must be a mapping")
        for key, value in layer.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = deep_merge([result[key], value])
            else:
                result[key] = value
    return result
