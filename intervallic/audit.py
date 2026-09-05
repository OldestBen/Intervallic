"""Audit Plex music library for incomplete albums and track-numbering issues."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from plexapi.server import PlexServer


@dataclass
class TrackIssue:
    track_number: Optional[int]
    title: str
    issue: str   # "no_number" | "duplicate" | "gap_before"


@dataclass
class AlbumReport:
    artist: str
    album: str
    year: Optional[int]
    total_tracks: int
    issues: List[TrackIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def missing_count(self) -> int:
        return sum(1 for i in self.issues if i.issue == "gap_before")

    @property
    def unnumbered_count(self) -> int:
        return sum(1 for i in self.issues if i.issue == "no_number")


def audit_library(
    url: str,
    token: str,
    section_name: Optional[str] = None,
) -> Tuple[List[AlbumReport], List[str]]:
    """
    Returns (reports, warnings).
    reports — one AlbumReport per album that has issues.
    warnings — non-fatal problems (e.g. section not found).
    """
    server   = PlexServer(url, token)
    warnings = []

    # Find music sections
    sections = [s for s in server.library.sections() if s.type == "artist"]
    if not sections:
        return [], ["No music library sections found on this Plex server."]

    if section_name:
        matched = [s for s in sections if s.title.lower() == section_name.lower()]
        if not matched:
            names = ", ".join(s.title for s in sections)
            warnings.append(f"Section '{section_name}' not found. Available: {names}")
            return [], warnings
        sections = matched

    reports = []
    for section in sections:
        for album in section.albums():
            try:
                tracks = album.tracks()
            except Exception as exc:
                warnings.append(f"Could not load tracks for '{album.title}': {exc}")
                continue

            report = _check_album(album, tracks)
            if not report.ok:
                reports.append(report)

    reports.sort(key=lambda r: (r.artist.lower(), r.album.lower()))
    return reports, warnings


def _check_album(album, tracks) -> AlbumReport:
    artist = album.grandparentTitle or ""
    year   = getattr(album, "year", None)

    report = AlbumReport(
        artist=artist,
        album=album.title,
        year=year,
        total_tracks=len(tracks),
    )

    numbered   = []
    seen       = set()

    for track in tracks:
        num = getattr(track, "index", None)
        if num is None:
            report.issues.append(TrackIssue(None, track.title, "no_number"))
        elif num in seen:
            report.issues.append(TrackIssue(num, track.title, "duplicate"))
        else:
            seen.add(num)
            numbered.append((num, track.title))

    # Check for gaps in the numbered sequence
    if numbered:
        numbered.sort()
        expected = numbered[0][0]
        for num, title in numbered:
            if num > expected:
                for missing in range(expected, num):
                    report.issues.append(TrackIssue(missing, f"(missing track {missing})", "gap_before"))
            expected = num + 1

    return report


def write_csv(reports: List[AlbumReport], path: str) -> None:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Artist", "Album", "Year", "Album Tracks", "Issue", "Track #", "Track Title"])
        for r in reports:
            for issue in r.issues:
                w.writerow([
                    r.artist, r.album, r.year or "", r.total_tracks,
                    issue.issue, issue.track_number or "", issue.title,
                ])
