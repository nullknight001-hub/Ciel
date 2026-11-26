import fcntl
import json
import os
from config import MEMORY_FILE


def load_memories() -> list[str]:
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _atomic_save_memory(entry_text: str) -> int:
    lock_path = MEMORY_FILE + ".lock"
    tmp_path = MEMORY_FILE + ".tmp"
    with open(lock_path, "w", encoding="utf-8") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            data = load_memories()
            if entry_text not in data:
                data.append(entry_text)
            with open(tmp_path, "w", encoding="utf-8") as tf:
                json.dump(data, tf, indent=2, ensure_ascii=False)
            os.replace(tmp_path, MEMORY_FILE)
            return len(data)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def save_memory(fact: str) -> str:
    """Saves an important fact, user preference, project path, or rule to permanent memory."""
    fact = fact.strip()
    if not fact:
        return "Ignored empty memory."
    before = len(load_memories())
    after = _atomic_save_memory(fact)
    if after > before:
        print(f"[Memory Saved]: {fact}")
        return f"Saved to permanent memory: {fact}"
    return "That fact is already in memory."


def delete_memory(keyword: str) -> str:
    """Removes any saved permanent memory containing the given keyword."""
    lock_path = MEMORY_FILE + ".lock"
    tmp_path = MEMORY_FILE + ".tmp"
    with open(lock_path, "w", encoding="utf-8") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            memories = load_memories()
            updated = [m for m in memories if keyword.lower() not in m.lower()]
            if len(updated) == len(memories):
                return f"No memory found matching '{keyword}'."
            with open(tmp_path, "w", encoding="utf-8") as tf:
                json.dump(updated, tf, indent=2, ensure_ascii=False)
            os.replace(tmp_path, MEMORY_FILE)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    print(f"[Memory Deleted]: matching '{keyword}'")
    return f"Removed memories matching '{keyword}'."