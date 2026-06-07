from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from plexapi.server import PlexServer


@dataclass
class PlexTrack:
    title: str
    artist: str
    album: str
    file_path: str  # absolute path as Plex sees it
    track_number: Optional[int] = None


@dataclass
class PlexPlaylist:
    name: str
    tracks: List[PlexTrack]


class PlexClient:
    def __init__(self, url: str, token: str) -> None:
        self._server = PlexServer(url, token)

    def get_playlists(
        self,
        include: List[str],
        exclude: List[str],
    ) -> List[PlexPlaylist]:
        raw_playlists = self._server.playlists()
        results = []

        for pl in raw_playlists:
            if pl.playlistType != "audio":
                continue
            if include and pl.title not in include:
                continue
            if pl.title in exclude:
                continue

            tracks = []
            for item in pl.items():
                media = item.media
                if not media:
                    continue
                # Take the first part of the first media item
                try:
                    file_path = media[0].parts[0].file
                except (IndexError, AttributeError):
                    continue

                tracks.append(
                    PlexTrack(
                        title=item.title or "",
                        artist=item.grandparentTitle or "",
                        album=item.parentTitle or "",
                        file_path=file_path,
                        track_number=getattr(item, "index", None),
                    )
                )

            results.append(PlexPlaylist(name=pl.title, tracks=tracks))

        return results
