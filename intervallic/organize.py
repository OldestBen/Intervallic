"""Sort downloaded "Album - Artist" items into per-artist folders.

The naming convention is  <album> - <artist>  with optional release-type
markers in between (e.g. "Burn The Bones - Single - Kodain"). Album titles
routinely contain hyphens ("Inside-Out", "1996-2006", "Re-Assemble"), so a
naive split is unsafe and a split on the *last* separator is merely lucky.

Instead we treat the directory listing as a corpus and let it validate itself:

  1. Items with exactly one separator are unambiguous. Their artists become
     "anchors".
  2. Anchors are counted. An artist appearing several times is strong evidence.
  3. Ambiguous items are scored against the anchor set plus structural rules
     (release markers and parenthetical qualifiers bind to the album side).
  4. Names are normalised — duplicate suffixes ("Small Faces-2") are stripped
     when the base name is a known artist, and characters stripped by the
     downloader ("Songs_ Ohia") are restored.

Every decision carries a confidence level and a human-readable reason so the
low-confidence minority can be reviewed before anything moves.
"""
from __future__ import annotations

import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SEPARATOR = " - "

# Release-type words. These describe the *release*, so they belong to the album
# side of the split and never begin an artist name.
_RELEASE_MARKERS = {
    "single", "ep", "remastered", "remaster", "live", "compilation",
    "deluxe edition", "special edition", "expanded edition",
    "anniversary edition", "bonus track version", "mono version",
}

# Characters Windows forbids in paths. Downloaders strip or substitute these.
_ILLEGAL = re.compile(r'[<>:"/\\|?*]')

# Trailing "-2", " (2)", "_3" left behind when a downloader avoids a collision.
_DEDUP_SUFFIX = re.compile(r"[-_ ]+\(?(\d{1,2})\)?$")

# Audio extensions that mark a directory as an actual album rather than a stray.
AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".alac", ".aac", ".ogg",
                    ".opus", ".wav", ".dsf", ".dff", ".wma", ".ape"}

CONFIDENCE_ORDER = {"certain": 0, "high": 1, "medium": 2, "low": 3}


@dataclass
class Item:
    """One thing in the source directory — a folder or an archive."""
    path: Path
    base: str          # name with any archive extension removed
    is_dir: bool


@dataclass
class Decision:
    """Where one item should go, and why."""
    item: Item
    album: str
    artist: str
    confidence: str            # certain | high | medium | low
    reason: str
    normalised_from: Optional[str] = None   # set when the raw artist was cleaned

    @property
    def needs_review(self) -> bool:
        return self.confidence in ("medium", "low") or self.normalised_from is not None


@dataclass
class Plan:
    root: Path
    decisions: List[Decision] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)   # (name, reason)

    @property
    def artists(self) -> List[str]:
        return sorted({d.artist for d in self.decisions}, key=str.lower)

    def by_artist(self) -> Dict[str, List[Decision]]:
        grouped: Dict[str, List[Decision]] = {}
        for d in self.decisions:
            grouped.setdefault(d.artist, []).append(d)
        for group in grouped.values():
            group.sort(key=lambda d: d.album.lower())
        return grouped

    @property
    def review_items(self) -> List[Decision]:
        return sorted(
            (d for d in self.decisions if d.needs_review),
            key=lambda d: (CONFIDENCE_ORDER[d.confidence], d.artist.lower()),
            reverse=True,
        )


# ── Candidate generation ──────────────────────────────────────────────────────

def _candidates(base: str) -> List[Tuple[str, str]]:
    """Every (album, artist) split of `base` on a separator, left to right."""
    out = []
    start = 0
    while True:
        idx = base.find(SEPARATOR, start)
        if idx == -1:
            break
        album = base[:idx].strip()
        artist = base[idx + len(SEPARATOR):].strip()
        if album and artist:
            out.append((album, artist))
        start = idx + 1
    return out


def _has_release_marker(artist: str) -> bool:
    """True if any segment of the candidate artist is a release-type word."""
    for segment in artist.split(SEPARATOR):
        if segment.strip().lower() in _RELEASE_MARKERS:
            return True
    return False


def _score(album: str, artist: str, anchors: Counter) -> Tuple[float, str]:
    """Score one candidate split. Higher is better. Returns (score, reason)."""
    score = 0.0
    reasons = []

    seen = anchors.get(artist.lower(), 0)
    if seen:
        score += 100 + min(seen, 5) * 10
        reasons.append(f"artist appears {seen}x elsewhere")

    if artist.lower() == album.lower():
        score += 40
        reasons.append("self-titled")

    if _has_release_marker(artist):
        score -= 60
        reasons.append("contains release marker")

    if "(" in artist:
        score -= 30
        reasons.append("contains qualifier")

    # Each separator still inside the artist means we probably split too early.
    remaining = artist.count(SEPARATOR)
    if remaining:
        score -= 15 * remaining
        reasons.append(f"{remaining} separator(s) remain")

    if len(artist.split()) <= 5:
        score += 5

    return score, "; ".join(reasons) if reasons else "structural default"


# ── Normalisation ─────────────────────────────────────────────────────────────

def _clean(artist: str) -> str:
    """Make a name safe for the filesystem without mangling it."""
    cleaned = _ILLEGAL.sub("_", artist)
    # A stripped colon leaves "Songs_ Ohia"; collapse it back to a space.
    cleaned = re.sub(r"_\s+", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip().rstrip(". ")


def _strip_dedup_suffix(artist: str, known: set) -> Optional[str]:
    """
    Remove a trailing duplicate marker, but only when doing so lands on an
    artist we have independent evidence for. "Small Faces-2" becomes
    "Small Faces" because "Small Faces" exists; "Blink-182" is left alone.
    """
    match = _DEDUP_SUFFIX.search(artist)
    if not match:
        return None
    stripped = artist[: match.start()].strip()
    if stripped and stripped.lower() in known:
        return stripped
    return None


# ── Planning ──────────────────────────────────────────────────────────────────

def _collect(root: Path, include_zips: bool) -> List[Item]:
    items = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            items.append(Item(path=entry, base=entry.name, is_dir=True))
        elif include_zips and entry.suffix.lower() in (".zip", ".rar", ".7z"):
            items.append(Item(path=entry, base=entry.stem, is_dir=False))
    return items


def build_plan(root: Path, include_zips: bool = True) -> Plan:
    """Work out where everything should go without touching the filesystem."""
    plan = Plan(root=root)
    items = _collect(root, include_zips)

    # Pass 1 — anchor on the unambiguous items.
    anchors: Counter = Counter()
    for item in items:
        cands = _candidates(item.base)
        if len(cands) == 1:
            anchors[cands[0][1].lower()] += 1

    # Pass 2 — decide every item.
    raw: List[Decision] = []
    for item in items:
        cands = _candidates(item.base)

        if not cands:
            plan.skipped.append((item.path.name, "no separator — already sorted?"))
            continue

        if len(cands) == 1:
            album, artist = cands[0]
            seen = anchors.get(artist.lower(), 0)
            if seen > 1:
                confidence, reason = "certain", f"artist appears {seen}x in this batch"
            elif artist.lower() == album.lower():
                confidence, reason = "certain", "self-titled album"
            else:
                confidence, reason = "high", "single separator"
            raw.append(Decision(item, album, artist, confidence, reason))
            continue

        # Ambiguous — score every split and take the best.
        scored = [(*_score(a, ar, anchors), a, ar) for a, ar in cands]
        scored.sort(key=lambda s: s[0], reverse=True)
        best_score, best_reason, album, artist = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else float("-inf")

        margin = best_score - runner_up
        if margin >= 60:
            confidence = "high"
        elif margin >= 25:
            confidence = "medium"
        else:
            confidence = "low"

        raw.append(Decision(item, album, artist, confidence, best_reason))

    # Pass 3 — normalise names now that we know the full artist set.
    known = {d.artist.lower() for d in raw}
    for d in raw:
        original = d.artist
        artist = _clean(original)

        deduped = _strip_dedup_suffix(artist, known)
        if deduped:
            artist = deduped

        if artist != original:
            d.normalised_from = original
            d.artist = artist

        plan.decisions.append(d)

    return plan


# ── Execution ─────────────────────────────────────────────────────────────────

def execute_plan(plan: Plan) -> Tuple[int, List[Tuple[str, str]]]:
    """Move everything. Returns (moved_count, failures)."""
    moved = 0
    failures: List[Tuple[str, str]] = []

    for d in plan.decisions:
        target_dir = plan.root / d.artist
        destination = target_dir / d.item.path.name

        if d.item.path.resolve() == target_dir.resolve():
            failures.append((d.item.path.name, "would move into itself"))
            continue
        if destination.exists():
            failures.append((d.item.path.name, f"already exists in '{d.artist}'"))
            continue

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d.item.path), str(destination))
            moved += 1
        except Exception as exc:
            failures.append((d.item.path.name, str(exc)))

    return moved, failures


def write_csv(plan: Plan, path: str) -> None:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Item", "Type", "Artist", "Album", "Confidence",
                    "Reason", "Normalised From", "Destination"])
        for d in sorted(plan.decisions, key=lambda d: (d.artist.lower(), d.album.lower())):
            w.writerow([
                d.item.path.name,
                "folder" if d.item.is_dir else "archive",
                d.artist,
                d.album,
                d.confidence,
                d.reason,
                d.normalised_from or "",
                str(plan.root / d.artist / d.item.path.name),
            ])
