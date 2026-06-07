from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass
class PlexConfig:
    url: str
    token: str
    playlist_filter: List[str] = field(default_factory=list)
    playlist_exclude: List[str] = field(default_factory=list)


@dataclass
class OutputConfig:
    directory: str
    format: str = "m3u8"
    overwrite: bool = True


@dataclass
class PathMapping:
    from_prefix: str
    to_prefix: str


@dataclass
class Config:
    plex: PlexConfig
    output: OutputConfig
    path_mapping: List[PathMapping] = field(default_factory=list)

    def remap_path(self, path: str) -> str:
        for mapping in self.path_mapping:
            if path.startswith(mapping.from_prefix):
                return mapping.to_prefix + path[len(mapping.from_prefix):]
        return path


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f)

    plex_raw = raw.get("plex", {})
    plex = PlexConfig(
        url=plex_raw["url"],
        token=plex_raw["token"],
        playlist_filter=plex_raw.get("playlist_filter") or [],
        playlist_exclude=plex_raw.get("playlist_exclude") or [],
    )

    out_raw = raw.get("output", {})
    output = OutputConfig(
        directory=out_raw["directory"],
        format=out_raw.get("format", "m3u8"),
        overwrite=out_raw.get("overwrite", True),
    )

    mappings = [
        PathMapping(from_prefix=m["from"], to_prefix=m["to"])
        for m in (raw.get("path_mapping") or [])
    ]

    return Config(plex=plex, output=output, path_mapping=mappings)
