"""Unit tests for backend.db.state — the SQLite persistence layer that
replaces registry.json (see the Master Architecture Document, section 6).

These exercise the repositories directly against a tmp_path database, the
same isolation pattern the existing platform tests use for STORE_PATH.
"""

from __future__ import annotations

import json

from backend.domain import (
    AgentRun,
    Bot,
    ChiefSpec,
    Project,
    ProjectEvent,
    Registry,
    RunKind,
    RunStatus,
    Team,
)
from backend.db.state import EventRepo, GlobalStateRepo, RegistryRepo, RunRepo, db
from backend.db.state.migrate_from_registry_json import migrate, needs_migration


def _registry() -> Registry:
    ekko = Bot(id="ekko", name="Ekko", role="developer", system_prompt="Build.")
    caitlyn = Bot(id="caitlyn", name="Caitlyn", role="qa", system_prompt="Test.", team_id="arcane")
    team = Team(id="arcane", name="Arcane", bot_ids=["ekko", "caitlyn"], lead_bot_id="ekko")
    project = Project(id="p1", name="Zaun App", team_ids=["arcane"], state={"kickoff": "build a widget"})
    project.remember("started work", role="bot", bot_id="ekko")
    chief = ChiefSpec(name="Chief", system_prompt="Route work.")
    return Registry(
        bots={"ekko": ekko, "caitlyn": caitlyn},
        teams={"arcane": team},
        projects={"p1": project},
        chief=chief,
        global_mcp_tools=["github"],
        global_tools=["terminal"],
    )


def test_registry_roundtrip(tmp_path) -> None:
    store_path = tmp_path / "registry.json"
    RegistryRepo.save(store_path, _registry())
    loaded = RegistryRepo.load(store_path)

    assert set(loaded.bots) == {"ekko", "caitlyn"}
    assert loaded.bots["caitlyn"].team_id == "arcane"
    assert loaded.teams["arcane"].bot_ids == ["ekko", "caitlyn"]
    assert loaded.projects["p1"].state == {"kickoff": "build a widget"}
    assert [e.text for e in loaded.projects["p1"].events] == ["started work"]
    assert loaded.chief.name == "Cat Crew"  # legacy "Chief" is read as Cat Crew
    assert loaded.global_mcp_tools == ["github"]
    assert loaded.global_tools == ["terminal"]

    # the .db file lives beside the legacy path, not at some fixed location
    assert db.db_path_for(store_path).exists()


def test_save_deletes_removed_catalog_rows(tmp_path) -> None:
    store_path = tmp_path / "registry.json"
    registry = _registry()
    RegistryRepo.save(store_path, registry)

    del registry.bots["caitlyn"]
    RegistryRepo.save(store_path, registry)

    loaded = RegistryRepo.load(store_path)
    assert set(loaded.bots) == {"ekko"}


def test_save_never_deletes_state_or_events(tmp_path) -> None:
    """Two independent in-memory Registry objects both saving (mimicking
    two bots delegating around the same time) must not clobber each
    other's project_state/events — that's the whole point of splitting
    them out of the catalog's destructive replace."""
    store_path = tmp_path / "registry.json"
    registry = _registry()
    RegistryRepo.save(store_path, registry)

    # bot A loads fresh, appends its own event, saves back
    a = RegistryRepo.load(store_path)
    a.projects["p1"].remember("bot A did a thing", role="bot", bot_id="a")
    RegistryRepo.save(store_path, a)

    # bot B, working from an earlier in-memory snapshot, appends its own
    # event and saves too — it must not erase bot A's event above.
    b = RegistryRepo.load(store_path)
    b.projects["p1"].remember("bot B did a thing", role="bot", bot_id="b")
    RegistryRepo.save(store_path, b)

    loaded = RegistryRepo.load(store_path)
    texts = {e.text for e in loaded.projects["p1"].events}
    assert {"started work", "bot A did a thing", "bot B did a thing"} <= texts


def test_global_state_repo_set_and_get(tmp_path) -> None:
    store_path = tmp_path / "registry.json"
    RegistryRepo.save(store_path, _registry())

    GlobalStateRepo.set(store_path, "p1", "tech_stack", ["python", "fastapi"], updated_by="chief")
    state = GlobalStateRepo.get(store_path, "p1")
    assert state["tech_stack"] == ["python", "fastapi"]
    # existing key from the original save is untouched
    assert state["kickoff"] == "build a widget"


def test_event_repo_append_is_concurrency_safe_by_id(tmp_path) -> None:
    store_path = tmp_path / "registry.json"
    RegistryRepo.save(store_path, _registry())

    event = ProjectEvent(role="bot", text="only once", bot_id="ekko")
    EventRepo.append(store_path, "p1", event)
    EventRepo.append(store_path, "p1", event)  # same id — must not duplicate

    recent = EventRepo.recent(store_path, "p1")
    matches = [e for e in recent if e.id == event.id]
    assert len(matches) == 1


def test_run_repo_lifecycle(tmp_path) -> None:
    store_path = tmp_path / "registry.json"
    RegistryRepo.save(store_path, _registry())

    parent = AgentRun.new(project_id="p1", bot_id="chief", kind=RunKind.CHIEF, task="ship it")
    RunRepo.create(store_path, parent)
    child = AgentRun.new(
        project_id="p1", bot_id="ekko", kind=RunKind.DELEGATED, task="build it", parent_run_id=parent.id
    )
    RunRepo.create(store_path, child)

    child.mark_done("done!")
    RunRepo.update(store_path, child)

    fetched = RunRepo.get(store_path, child.id)
    assert fetched is not None
    assert fetched.status == RunStatus.DONE
    assert fetched.summary == "done!"

    kids = RunRepo.children(store_path, parent.id)
    assert [k.id for k in kids] == [child.id]

    for_bot = RunRepo.for_bot(store_path, "p1", "ekko")
    assert [r.id for r in for_bot] == [child.id]


def test_migration_imports_legacy_json_once(tmp_path) -> None:
    store_path = tmp_path / "registry.json"
    store_path.write_text(json.dumps(_registry().to_dict()), encoding="utf-8")

    assert needs_migration(store_path) is True
    migrated = migrate(store_path)
    assert migrated is not None
    assert set(migrated.bots) == {"ekko", "caitlyn"}

    # legacy file renamed aside, never deleted
    assert not store_path.exists()
    assert (tmp_path / "registry.json.migrated").exists()

    # a second call is a no-op — nothing left to migrate, and the DB
    # already has a real catalog either way
    assert needs_migration(store_path) is False
    assert migrate(store_path) is None


def test_migration_never_overwrites_an_already_seeded_database(tmp_path) -> None:
    store_path = tmp_path / "registry.json"
    RegistryRepo.save(store_path, _registry())  # seed the DB directly first

    # now a stray legacy JSON file with different content shows up
    other = Registry(bots={"solo": Bot(id="solo", name="Solo", role="general", system_prompt="x")})
    store_path.write_text(json.dumps(other.to_dict()), encoding="utf-8")

    assert needs_migration(store_path) is False
    assert migrate(store_path) is None

    loaded = RegistryRepo.load(store_path)
    assert set(loaded.bots) == {"ekko", "caitlyn"}  # untouched
