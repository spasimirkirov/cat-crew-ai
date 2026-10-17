"""Example catalog: Software Engineering + Accountant teams on a marketplace project."""

from __future__ import annotations

from .chief import DEFAULT_CHIEF_PROMPT
from .models import Registry
from .registry import PlatformBuilder

ARCHITECT_PROMPT = """You are the Architect on the Software Engineering team.
Design structure, APIs, data models, and file layout. Prefer small, clear plans.
Do not write production code unless Chief asked for a skeleton. Read the workspace first.
Return: architecture notes, file list, and what Developer / Designer / QA should do next.
"""

DESIGNER_PROMPT = """You are the Designer on the Software Engineering team.
Own UX copy, layout, visual hierarchy, and static HTML/CSS when asked.
You may generate reference images. Keep the look consistent with the architecture brief.
Return: what you changed and remaining design gaps.
"""

DEVELOPER_PROMPT = """You are the Developer on the Software Engineering team.
Implement features with small patches. Tools first: read, then replace_in_file / write_file.
Use the shell for installs and tests. Do not claim done without checking the files you touched.
Return: paths changed, how to run, and what QA should verify.
"""

QA_PROMPT = """You are QA on the Software Engineering team.
Review the workspace against the task. Run tests or sanity-check the UI/API.
Report bugs with file paths and reproduction steps. Do not rewrite features unless asked.
Return: pass/fail, bugs, and suggested fixes for Developer.
"""

ACCOUNTANT_PROMPT = """You are the Accountant team's lead.
Handle pricing, invoices, tax notes, and marketplace fee math. You may search the web
for public rates. You cannot edit application source unless Chief explicitly says so.
Return: numbers, assumptions, and any documents/paths you created.
"""


def marketplace_builder() -> PlatformBuilder:
    """Software Engineering Team + Accountant Team on Simple Marketplace Website."""
    plat = PlatformBuilder()
    plat.global_tools("read_skill", "system_time")
    plat.chief(
        name="Chief",
        prompt=DEFAULT_CHIEF_PROMPT,
        mcp_tools=[],
        max_rounds=12,
    )
    plat.team(
        "software-eng",
        "Software Engineering Team",
        "Architecture, UX, implementation, and QA for product work.",
    )
    plat.bot(
        "architect",
        "Architect",
        role="architect",
        prompt=ARCHITECT_PROMPT,
        lead=True,
        description="Plans specs and system shape.",
    )
    plat.bot(
        "designer",
        "Designer",
        role="designer",
        prompt=DESIGNER_PROMPT,
        mcp_tools=[],
        description="UX and static UI.",
    )
    plat.bot(
        "developer",
        "Developer",
        role="developer",
        prompt=DEVELOPER_PROMPT,
        description="Implements the product in the workspace.",
    )
    plat.bot(
        "qa",
        "QA",
        role="qa",
        prompt=QA_PROMPT,
        description="Tests and regression notes.",
    )
    plat.team(
        "accountant",
        "Accountant Team",
        "Pricing, fees, invoices, and marketplace economics.",
    )
    plat.bot(
        "accountant",
        "Accountant",
        role="accountant",
        prompt=ACCOUNTANT_PROMPT,
        lead=True,
        description="Numbers and commercial constraints.",
    )
    plat.project(
        "marketplace",
        "Simple Marketplace Website",
        "A small marketplace: listings, cart, checkout, and seller payouts.",
        teams=["software-eng", "accountant"],
        state={"goal": "Ship a simple marketplace website."},
    )
    return plat


def marketplace_registry() -> Registry:
    return marketplace_builder().build()


def seed_marketplace(*, overwrite: bool = False) -> Registry:
    """Write the example catalog to data/platform/registry.json."""
    from .registry import load_registry, save_registry

    current = load_registry(refresh=True)
    if current.projects and not overwrite:
        return current
    return marketplace_builder().save()
