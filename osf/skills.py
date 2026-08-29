"""Skills — reusable, named capability instructions a worker loads when relevant.

Mirrors opencode/Claude "skills": each `Skill` has a name, a short `description` (used to decide
when it applies), and `instructions` that get composed into the worker's prompt. Skills keep domain
expertise out of the base prompt and add it on demand, so a generic worker becomes a specialist for
a given WorkItem (e.g. a "new-repo" skill for repository scaffolding).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str  # when the skill applies — the progressive-disclosure hint
    instructions: str  # guidance injected into the worker prompt when the skill is active


class SkillRegistry:
    """A named collection of skills that renders their instructions on demand."""

    def __init__(self, skills: Iterable[Skill] = ()) -> None:
        self._skills: dict[str, Skill] = {}
        for skill in skills:
            self.register(skill)

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"duplicate skill {skill.name!r}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        return self._skills[name]

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def render(self, names: Iterable[str]) -> str:
        """Compose the instructions for the named skills, in order."""
        skills = [self._skills[n] for n in names]
        return "\n\n".join(f"## Skill: {s.name}\n{s.instructions}" for s in skills)


def apply_skills(base_prompt: str, registry: SkillRegistry, names: Iterable[str]) -> str:
    """Append the rendered skill instructions to a base worker prompt."""
    rendered = registry.render(names)
    if not rendered:
        return base_prompt
    return f"{base_prompt}\n\n---\nApply these skills:\n\n{rendered}"
