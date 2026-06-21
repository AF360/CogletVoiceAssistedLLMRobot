"""Command helpers shared by Coglet launchers."""
import re
def normalize_command_text(text: str) -> str:
    text=re.sub(r"[^\wäöüß\s-]+"," ",(text or "").lower())
    return re.sub(r"\s+"," ",text).strip()
