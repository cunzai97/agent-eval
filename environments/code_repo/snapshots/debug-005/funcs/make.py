from .state import consume


def make_processors(specs):
    processors = {}
    for spec in specs:
        name = spec["name"]

        def process(value):
            consume(name, spec["limit"])
            return value + spec["offset"]

        processors[name] = process
    return processors
