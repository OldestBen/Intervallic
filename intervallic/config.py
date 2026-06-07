from __future__ import annotations

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
class SftpConfig:
    host: str
    remote_directory: str
    port: int = 22
    username: str = ""
    password: Optional[str] = None
    key_path: Optional[str] = None  # path to private key file


@dataclass
class OutputConfig:
    # For local output: path on this machine (or a mounted share)
    directory: Optional[str] = None
    # For remote SFTP output
    sftp: Optional[SftpConfig] = None
    format: str = "m3u8"
    overwrite: bool = True

    def validate(self) -> None:
        if not self.directory and not self.sftp:
            raise ValueError("output must specify either 'directory' or 'sftp'")


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
    sftp_raw = out_raw.get("sftp")
    sftp = None
    if sftp_raw:
        sftp = SftpConfig(
            host=sftp_raw["host"],
            remote_directory=sftp_raw["remote_directory"],
            port=sftp_raw.get("port", 22),
            username=sftp_raw.get("username", ""),
            password=sftp_raw.get("password"),
            key_path=sftp_raw.get("key_path"),
        )

    output = OutputConfig(
        directory=out_raw.get("directory"),
        sftp=sftp,
        format=out_raw.get("format", "m3u8"),
        overwrite=out_raw.get("overwrite", True),
    )
    output.validate()

    mappings = [
        PathMapping(from_prefix=m["from"], to_prefix=m["to"])
        for m in (raw.get("path_mapping") or [])
    ]

    return Config(plex=plex, output=output, path_mapping=mappings)


def save_config(cfg_dict: dict, path: str) -> None:
    with open(path, "w") as f:
        yaml.dump(cfg_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
