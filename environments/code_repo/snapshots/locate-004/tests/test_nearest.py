from geo.nearest import nearest


def test_ordinary_longitudes():
    assert nearest((0, 0), [(2, 0), (10, 0)]) == (2, 0)


def test_empty_candidates():
    assert nearest((0, 0), []) is None


def test_antimeridian_wraparound():
    assert nearest((179, 0), [(-179, 0), (170, 0)]) == (-179, 0)
