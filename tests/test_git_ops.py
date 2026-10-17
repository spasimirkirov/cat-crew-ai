from backend.services.git_ops import split_status_files, status_letter


def test_untracked_is_changes_only() -> None:
    staged, changes = split_status_files([{"xy": "??", "path": "a.py"}])
    assert staged == []
    assert len(changes) == 1
    assert changes[0]["letter"] == "U"
    assert changes[0]["path"] == "a.py"


def test_index_modified_is_staged_only() -> None:
    staged, changes = split_status_files([{"xy": "M ", "path": "a.py"}])
    assert len(staged) == 1 and staged[0]["letter"] == "M"
    assert changes == []


def test_worktree_modified_is_changes_only() -> None:
    staged, changes = split_status_files([{"xy": " M", "path": "a.py"}])
    assert staged == []
    assert len(changes) == 1 and changes[0]["letter"] == "M"


def test_mm_appears_in_both_lists() -> None:
    staged, changes = split_status_files([{"xy": "MM", "path": "a.py"}])
    assert staged[0]["letter"] == "M"
    assert changes[0]["letter"] == "M"


def test_status_letter_untracked() -> None:
    assert status_letter("??", staged_list=False) == "U"
    assert status_letter("A ", staged_list=True) == "A"
    assert status_letter(" D", staged_list=False) == "D"
