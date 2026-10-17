from pathlib import Path

from jinx_ai.single_instance import InstanceLock, acquire, release


def test_second_lock_on_same_file_fails(tmp_path: Path) -> None:
    path = tmp_path / "jinx.lock"
    first = InstanceLock(path)
    second = InstanceLock(path)
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_app_acquire_blocks_second(tmp_path: Path) -> None:
    path = tmp_path / "jinx.lock"
    release()
    try:
        assert acquire(path)
        other = InstanceLock(path)
        assert not other.acquire()
        release()
        assert other.acquire()
        other.release()
    finally:
        release()
