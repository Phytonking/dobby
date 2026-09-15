"""Name -> email memory so "invite Maya" works after Dobby is told her address once.

Stored as JSON at Config.contacts_file (default data/contacts.json). Writes are atomic
(temp file + replace) and serialized with a lock because the scheduler thread reads
while the Discord event loop writes.
"""

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def valid_email(text):
    return bool(text) and len(text) <= 254 and EMAIL.match(text) is not None


def normalize(name):
    return " ".join(str(name).split()).lower()


class Contacts:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            return {}
        contacts = data.get("contacts") if isinstance(data, dict) else None
        return contacts if isinstance(contacts, dict) else {}

    def _write(self, contacts):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        with open(temp, "w", encoding="utf-8") as stream:
            json.dump({"contacts": contacts}, stream, indent=2, sort_keys=True)
        os.replace(temp, self.path)

    def lookup(self, names):
        """Split invitee names into {name: email} for known ones and a list of unknown ones.

        A name that is already an email address passes straight through. "Maya Chen" finds
        a contact saved as "Maya", and "Maya" finds one saved as "Maya Chen" if only one
        contact has that first name.
        """
        with self.lock:
            contacts = self._read()
        found, missing = {}, []
        for raw in names:
            name = " ".join(str(raw).split()).strip("<>")
            if not name:
                continue
            if valid_email(name):
                found[name] = name.lower()
                continue
            key = normalize(name)
            entry = contacts.get(key) or contacts.get(key.split(" ")[0])
            if entry is None:
                # A first name alone finds a contact saved with a full name, when unambiguous.
                by_first = [v for k, v in contacts.items() if k.split(" ")[0] == key]
                entry = by_first[0] if len(by_first) == 1 else None
            if entry and valid_email(entry.get("email", "")):
                found[name] = entry["email"]
            elif name not in missing:
                missing.append(name)
        return found, missing

    def save(self, name, email, added_by=None):
        display = " ".join(str(name).split())
        key = normalize(display)
        email = str(email).strip().strip("<>").lower()
        if not key or len(display) > 100:
            raise ValueError("name")
        if not valid_email(email):
            raise ValueError("email")
        with self.lock:
            contacts = self._read()
            contacts[key] = {
                "email": email,
                "display": display,
                "added_by": added_by,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            self._write(contacts)
        return display

    def remove(self, name):
        key = normalize(name)
        with self.lock:
            contacts = self._read()
            if key not in contacts:
                return False
            del contacts[key]
            self._write(contacts)
        return True

    def all(self):
        """{display name: email}, sorted by name."""
        with self.lock:
            contacts = self._read()
        rows = {v.get("display", k): v.get("email", "") for k, v in contacts.items()}
        return dict(sorted(rows.items(), key=lambda item: item[0].lower()))


def mask(email):
    """m***@example.com, so listings never print full addresses into a channel."""
    local, _, domain = email.partition("@")
    return (local[:1] + "***@" + domain) if domain else "***"


NAME_EMAIL = re.compile(
    r"(?P<name>[^\s:=\-–—<>@,;]+(?:\s+[^\s:=\-–—<>@,;]+){0,3})\s*(?:\bis\b|:|=|-|–|—|→)\s*<?(?P<email>[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+)>?",
    re.IGNORECASE,
)
ANY_EMAIL = re.compile(r"<?([^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+)>?")


def parse_pairs(text, outstanding):
    """Map a reply like "Maya: maya@x.com, Leonard is leo@y.org" onto outstanding names.

    Returns {outstanding name: email}. A bare email with exactly one outstanding name
    is assigned to that name. Names are matched case-insensitively, also by first word.
    """
    found = {}
    lookup = {normalize(n): n for n in outstanding}
    first_words = {normalize(n).split(" ")[0]: n for n in outstanding}
    for match in NAME_EMAIL.finditer(text):
        name, email = match.group("name").strip(), match.group("email").lower()
        if not valid_email(email):
            continue
        target = lookup.get(normalize(name)) or first_words.get(normalize(name).split(" ")[-1])
        if target and target not in found:
            found[target] = email
    if not found:
        emails = [e.lower() for e in ANY_EMAIL.findall(text) if valid_email(e)]
        remaining = [n for n in outstanding if n not in found]
        if len(emails) == 1 and len(remaining) == 1:
            found[remaining[0]] = emails[0]
    return found
