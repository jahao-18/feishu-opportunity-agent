from app_security import PublicDemoPolicy, consume_analysis_quota


def policy(**overrides) -> PublicDemoPolicy:
    values = {
        "enabled": True,
        "analysis_limit": 2,
        "window_seconds": 60,
        "public_feishu_save_enabled": False,
        "write_access_code": "",
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
    assert not policy().can_attempt_feishu_save
    assert policy(public_feishu_save_enabled=True).can_attempt_feishu_save
    assert policy(enabled=False).can_attempt_feishu_save


def test_public_save_requires_the_server_side_write_code():
    guarded = policy(
        public_feishu_save_enabled=True,
        write_access_code="FDE-demo-code",
    )

    assert guarded.requires_write_code
    assert guarded.write_code_configured
    assert not guarded.authorize_feishu_save()
    assert not guarded.authorize_feishu_save("wrong-code")
    assert guarded.authorize_feishu_save("FDE-demo-code")


def test_public_save_fails_closed_when_write_code_is_missing():
    guarded = policy(public_feishu_save_enabled=True)

    assert guarded.requires_write_code
    assert not guarded.write_code_configured
    assert not guarded.authorize_feishu_save("anything")


def test_local_save_does_not_require_a_write_code():
    assert policy(enabled=False).authorize_feishu_save()


def test_policy_repr_does_not_expose_write_code():
    guarded = policy(
        public_feishu_save_enabled=True,
        write_access_code="never-show-this",
    )

    assert "never-show-this" not in repr(guarded)
