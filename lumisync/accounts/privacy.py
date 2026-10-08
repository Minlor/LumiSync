"""Display hints for accounts without exposing complete email addresses."""

import re


def mask_email(email: str) -> str:
    local, separator, domain = str(email).strip().partition("@")
    if not separator or not local or not domain:
        return "Hidden email"
    suffix = domain.rsplit(".", 1)[-1] if "." in domain else ""
    suffix = "." + suffix if suffix.isalpha() and len(suffix) <= 24 else ""
    return local[0] + "•••@•••" + suffix


def mask_account_label(label: str) -> str:
    # Also redact labels persisted by versions that included the full address.
    return re.sub(r"[^\s@·]+@[^\s@·]+", lambda match: mask_email(match.group()), str(label))
