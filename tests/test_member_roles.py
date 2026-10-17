from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

from jinx_ai.member_avatar import CircleCropView, circle_mask, generic_role_pixmap, member_pixmap
from jinx_ai.platform.roles import apply_role_fields, role_preset
from jinx_ai.platform_dialogs import BotDialog


def test_role_fills_defaults_and_keeps_custom_skills() -> None:
    title, skills, prompt = apply_role_fields("developer", "")
    assert title == "Developer"
    assert "implementation" in skills
    assert "replace_in_file" in prompt
    title2, skills2, prompt2 = apply_role_fields(
        "qa",
        "developer",
        title="Developer",
        skills="my custom skill",
        prompt=prompt,
    )
    assert title2 == "QA"
    assert skills2 == "my custom skill"
    assert "pass/fail" in prompt2


def test_catalog_includes_new_roles() -> None:
    from jinx_ai.platform.roles import role_ids

    ids = role_ids()
    assert ids[0] == "lead"
    for role in ("legal", "pr", "marketing", "assistant", "devops", "morale"):
        assert role in ids
        preset = role_preset(role)
        assert preset.skills
        assert preset.prompt
    assert role_preset("lawyer").id == "legal"
    assert role_preset("Morale Specialist").id == "morale"
    assert role_preset("lead").title == "Team Lead"


def test_bio_is_not_part_of_role_preset() -> None:
    preset = role_preset("architect")
    assert preset.skills
    assert "architecture notes" in preset.prompt
    assert not hasattr(preset, "bio")


def test_member_dialog_role_and_generic_photo() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    dlg = BotDialog()
    assert "research" in dlg.skills.text()
    assert dlg.details.placeholderText().lower().startswith("optional")
    assert not dlg._ok.isEnabled()
    idx = dlg.role.findData("designer")
    dlg.role.setCurrentIndex(idx)
    assert dlg.title.text() == "Designer"
    assert "UX" in dlg.skills.text()
    assert "HTML/CSS" in dlg.prompt.toPlainText()
    dlg.name.setText("Nia")
    assert dlg._ok.isEnabled()
    assert dlg.minimumWidth() >= 780
    bot = dlg.result_bot()
    assert bot is not None
    assert bot.name == "Nia"
    assert bot.role == "designer"
    assert bot.description == ""
    assert bot.avatar == ""
    pix = dlg.preview.pixmap()
    assert pix is not None and not pix.isNull()
    image = pix.toImage()
    assert image.pixelColor(0, 0).alpha() < 40
    assert image.pixelColor(48, 48).alpha() > 80


def test_circle_crop_and_generic_glyph(tmp_path) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    src = QPixmap(160, 80)
    src.fill(QColor("#ff4d6d"))
    path = tmp_path / "wide.png"
    src.save(str(path), "PNG")
    view = CircleCropView()
    view.set_image(str(path))
    out = view.cropped(32)
    assert out.width() == 32
    assert out.height() == 32
    img = out.toImage()
    assert img.pixelColor(0, 0).alpha() < 40
    assert img.pixelColor(16, 16).alpha() > 200
    generic = generic_role_pixmap("qa", 40)
    assert generic.width() == 40
    masked = circle_mask(src, 24)
    assert masked.width() == 24
    empty = member_pixmap("", "accountant", 36)
    assert empty.width() == 36


def test_member_dialog_photo_stays_beside_form(tmp_path) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    src = QPixmap(120, 120)
    src.fill(QColor("#22d3ee"))
    path = tmp_path / "face.png"
    src.save(str(path), "PNG")
    dlg = BotDialog()
    dlg.crop.set_image(str(path))
    dlg.crop.show()
    dlg._zoom_row.show()
    dlg._refresh_preview()
    assert dlg.crop.parentWidget() is not dlg
    assert dlg.crop.parentWidget() is not dlg.name.parentWidget()
    assert dlg.minimumWidth() >= 780


def test_project_dialog_computer_teams_and_kickoff(tmp_path, monkeypatch) -> None:
    from PyQt6.QtCore import Qt

    from jinx_ai.platform.registry import PlatformBuilder, reset_registry_cache
    from jinx_ai.platform_dialogs import ProjectDialog

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    store = tmp_path / "registry.json"
    monkeypatch.setattr("jinx_ai.platform.registry.STORE_PATH", store)
    reset_registry_cache()
    try:
        PlatformBuilder().team("arcane", "Arcane").save()
        dlg = ProjectDialog()
        dlg.name.setText("Shop")
        dlg.folder.setText(str(tmp_path / "shop"))
        dlg.kickoff.setPlainText("Build the product page.")
        item = dlg.teams.item(0)
        assert item is not None
        item.setCheckState(Qt.CheckState.Checked)
        assert dlg.kickoff_text() == "Build the product page."
        project = dlg.result_project()
        assert project is not None
        assert project.name == "Shop"
        assert project.team_ids == ["arcane"]
        assert Path(project.folders[0]).resolve() == (tmp_path / "shop").resolve()
        assert (tmp_path / "shop").is_dir()
        edit = ProjectDialog(project)
        assert edit.kickoff_text() == ""
    finally:
        reset_registry_cache()
