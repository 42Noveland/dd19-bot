from core.jrrp import calc_jrrp


def test_deterministic_same_input_same_output():
    assert calc_jrrp(10001, "2026-10-02") == calc_jrrp(10001, "2026-10-02")


def test_value_range_0_to_100():
    for uid in range(10000, 10100):
        assert 0 <= calc_jrrp(uid, "2026-10-02") <= 100


def test_snapshot_values_do_not_change():
    # 基线快照：算法被改动会立刻报警
    assert calc_jrrp(10001, "2026-10-02") == 27
    assert calc_jrrp(20002, "2026-10-02") == 12
    assert calc_jrrp(10001, "2026-10-03") == 9
