import os
from config import BASE_DIR, MAX_OUTPUT_CHARS

SKILLS_DIR = os.path.join(BASE_DIR, "skills")


def list_skills() -> list[str]:
    if not os.path.isdir(SKILLS_DIR):
        return []
    return sorted(
        f[:-3] for f in os.listdir(SKILLS_DIR) if f.endswith(".md")
    )


def load_skill(name: str) -> str:
    """Loads a step-by-step troubleshooting or task playbook from the skills/ folder."""
    clean = name.strip().removesuffix(".md")
    path = os.path.join(SKILLS_DIR, f"{clean}.md")
    if not os.path.exists(path):
        available = ", ".join(list_skills()) or "none"
        return f"Skill '{clean}' not found. Available skills: {available}"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()[:MAX_OUTPUT_CHARS]