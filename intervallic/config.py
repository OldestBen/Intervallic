from __future__ import annotations

from dataclasses import dataclass, field
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
    key_path: Optional[str] = None


@dataclass
class SmbConfig:
    # UNC-style: server=192.168.1.10, share=music, directory=Playlists
    server: str
    share: str
    directory: str = ""        # subdirectory within the share (may be empty)
    username: str = ""
    password: Optional[str] = None
    domain: str = ""

    @property
    def unc_directory(self) -> str:
        """Full UNC path: //server/share/directory"""
        base = f"//{self.server}/{self.share}"
        if self.directory:
            return base + "/" + self.directory.lstrip("/")
        return base


@dataclass
class OutputConfig:
    directory: Optional[str] = None   # local path
    sftp: Optional[SftpConfig] = None
    smb: Optional[SmbConfig] = None
    format: str = "m3u8"
    overwrite: bool = True

    def validate(self) -> None:
        if not any([self.directory, self.sftp, self.smb]):
            raise ValueError("output must specify directory, sftp, or smb")


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

    sftp = None
    if out_raw.get("sftp"):
        s = out_raw["sftp"]
        sftp = SftpConfig(
            host=s["host"],
            remote_directory=s["remote_directory"],
            port=s.get("port", 22),
            username=s.get("username", ""),
            password=s.get("password"),
            key_path=s.get("key_path"),
        )

    smb = None
    if out_raw.get("smb"):
        s = out_raw["smb"]
        smb = SmbConfig(
            server=s["server"],
            share=s["share"],
            directory=s.get("directory", ""),
            username=s.get("username", ""),
            password=s.get("password"),
            domain=s.get("domain", ""),
        )

    output = OutputConfig(
        directory=out_raw.get("directory"),
        sftp=sftp,
        smb=smb,
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
