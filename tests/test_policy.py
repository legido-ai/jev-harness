import pytest

from jev_harness import Policy
from jev_harness.policy import PolicyError


def test_threshold_lookup_order():
    policy = Policy(thresholds={"lead": 0.8}, default_threshold=0.9)
    assert policy.threshold_for("lead") == 0.8
    assert policy.threshold_for("customer") == 0.9


def test_destructive_action_cannot_be_lowered():
    policy = Policy(thresholds={"refund": 0.2}, destructive={"refund"}, destructive_threshold=0.95)
    assert policy.threshold_for("refund") == 0.95
    assert policy.may_act("refund", 0.94) is False
    assert policy.may_act("refund", 0.96) is True


def test_missing_confidence_never_acts():
    policy = Policy()
    assert policy.may_act("anything", None) is False
    assert policy.needs_review(None) is True


def test_out_of_range_thresholds_rejected():
    with pytest.raises(PolicyError):
        Policy(thresholds={"x": 1.5})
    with pytest.raises(PolicyError):
        Policy(default_threshold=-0.1)


def test_review_floor_cannot_exceed_destructive_threshold():
    with pytest.raises(PolicyError):
        Policy(review_floor=0.99, destructive_threshold=0.9)


def test_needs_review_uses_floor():
    policy = Policy(review_floor=0.5)
    assert policy.needs_review(0.49) is True
    assert policy.needs_review(0.5) is False
