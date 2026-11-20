from .dist import dist


def nearest(point, candidates):
    if not candidates:
        return None
    return min(candidates, key=lambda item: (dist(point, item), item))
