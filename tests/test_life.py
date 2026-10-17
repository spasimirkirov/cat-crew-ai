from datetime import datetime, timedelta, timezone

from jinx_ai.life import clip_note, decide, is_sleep_window, log_event, save_state
from jinx_ai.orb_particles import ParticleField


def test_sleep_window_overnight() -> None:
    now = datetime(2026, 9, 8, 23, 0, tzinfo=timezone.utc)
    assert is_sleep_window(now, 22, 7)
    morning = datetime(2026, 9, 9, 6, 0, tzinfo=timezone.utc)
    assert is_sleep_window(morning, 22, 7)
    day = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    assert not is_sleep_window(day, 22, 7)


def test_decide_wraps_after_evening_idle() -> None:
    now = datetime(2026, 9, 8, 22, 20, tzinfo=timezone.utc)
    last = (now - timedelta(minutes=20)).isoformat()
    action = decide(
        now,
        enabled=True,
        phase="awake",
        day="2026-09-08",
        last_user=last,
        last_wrapup_day="",
        busy=False,
        sleep_hour=22,
        wake_hour=7,
        idle_sleep_min=15,
        mind_every=5,
        ticks=2,
    )
    assert action == "wrap"


def test_decide_skips_sleep_and_wakes_in_the_morning() -> None:
    night = datetime(2026, 9, 8, 23, 10, tzinfo=timezone.utc)
    assert (
        decide(
            night,
            enabled=True,
            phase="sleeping",
            day="2026-09-08",
            last_user=now_iso(night),
            last_wrapup_day="2026-09-08",
            busy=False,
            sleep_hour=22,
            wake_hour=7,
            idle_sleep_min=15,
            mind_every=5,
            ticks=9,
        )
        == "skip_sleep"
    )
    morning = datetime(2026, 9, 9, 7, 5, tzinfo=timezone.utc)
    assert (
        decide(
            morning,
            enabled=True,
            phase="sleeping",
            day="2026-09-08",
            last_user=now_iso(night),
            last_wrapup_day="2026-09-08",
            busy=False,
            sleep_hour=22,
            wake_hour=7,
            idle_sleep_min=15,
            mind_every=5,
            ticks=9,
        )
        == "wake"
    )


def test_decide_busy_and_thought() -> None:
    now = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)
    last = now.isoformat()
    assert (
        decide(
            now,
            enabled=True,
            phase="awake",
            day="2026-09-08",
            last_user=last,
            last_wrapup_day="",
            busy=True,
            sleep_hour=22,
            wake_hour=7,
            idle_sleep_min=15,
            mind_every=5,
            ticks=5,
        )
        == "skip_busy"
    )
    assert (
        decide(
            now,
            enabled=True,
            phase="awake",
            day="2026-09-08",
            last_user=last,
            last_wrapup_day="",
            busy=False,
            sleep_hour=22,
            wake_hour=7,
            idle_sleep_min=15,
            mind_every=5,
            ticks=5,
        )
        == "thought"
    )


def test_sleeping_does_not_log(monkeypatch, tmp_path) -> None:
    import jinx_ai.life as life

    monkeypatch.setattr(life, "LIFE_DIR", tmp_path)
    monkeypatch.setattr(life, "STATE_PATH", tmp_path / "state.json")
    save_state({"phase": "sleeping", "day": "2026-09-08"})
    assert log_event("heartbeat", "should not write") is None
    assert log_event("wake", "User is here") is not None


def test_clip_note_shortens() -> None:
    assert len(clip_note("x" * 400)) <= 240


def test_sleeping_orb_mode() -> None:
    field = ParticleField(80)
    field.set_mode("sleeping")
    assert field.mode == "sleeping"


def now_iso(now: datetime) -> str:
    return now.isoformat()
