"""Chart thinning. One function, because the same defect was written four times."""

from pipeline.metrics import series


def test_the_newest_point_survives_an_even_length_series():
    """`points[::2]` keeps 0, 2, 4... so it drops the last element whenever
    len-1 is not a multiple of the step. The newest point is the one the panel
    prints beside the chart."""
    points = [{"d": i} for i in range(10)]
    thinned = series.keep_newest(points, 2)
    assert thinned[-1] == {"d": 9}
    assert thinned[0] == {"d": 0}


def test_the_newest_point_is_not_duplicated_when_the_slice_already_has_it():
    points = [{"d": i} for i in range(11)]
    thinned = series.keep_newest(points, 2)
    assert thinned[-1] == {"d": 10}
    assert thinned.count({"d": 10}) == 1


def test_a_step_of_three_keeps_the_newest_at_every_length():
    """The live defect was `[::3]` on a 540-point series, which ended two days
    before the value printed next to it."""
    for n in range(1, 40):
        points = [{"d": i} for i in range(n)]
        assert series.keep_newest(points, 3)[-1] == {"d": n - 1}, n


def test_every_step_keeps_the_newest_at_every_length():
    for step in (2, 3, 4):
        for n in range(1, 30):
            points = [{"d": i} for i in range(n)]
            assert series.keep_newest(points, step)[-1] == {"d": n - 1}, (step, n)


def test_an_empty_series_thins_to_empty():
    assert series.keep_newest([], 3) == []


def test_thinning_actually_thins():
    points = [{"d": i} for i in range(100)]
    assert len(series.keep_newest(points, 4)) < 30
