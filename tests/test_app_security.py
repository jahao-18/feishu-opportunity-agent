from app_security import PublicDemoPolicy, consume_analysis_quota


def policy(**overrides) -> PublicDemoPolicy:
    values = {
        "enabled": True,
        "analysis_limit": 2,
        "window_seconds": 60,
        "public_feishu_save_enabled": False,
    }
    values.update(overrides)
    return PublicDemoPolicy(**values)


def test_public_demo_rate_limit_accepts_then_rejects():
    timestamps, retry = consume_analysis_quota([], policy(), now=100)
    assert retry == 0
    timestamps, retry = consume_analysis_quota(timestamps, policy(), now=110)
    assert retry == 0
    timestamps, retry = consume_analysis_quota(timestamps, policy(), now=120)
    assert retry == 40


def test_expired_requests_release_quota():
    timestamps, retry = consume_analysis_quota([10, 20], policy(), now=80)
    assert retry == 0
    assert timestamps == [80]


def test_local_mode_does_not_consume_or_reject_quota():
    timestamps, retry = consume_analysis_quota(
        [10, 20], policy(enabled=False), now=30
    )
    assert retry == 0
    assert timestamps == [10, 20]


def test_public_save_is_disabled_by_default_policy():
    assert not policy().can_save_to_feishu
    assert policy(public_feishu_save_enabled=True).can_save_to_feishu
    assert policy(enabled=False).can_save_to_feishu
