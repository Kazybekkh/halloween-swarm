"""Environment loading and helpers shared by every entry point."""
import glob
import os
import re
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TEAM_RE = re.compile(r"^[a-z0-9-]{2,32}$")


def need(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set in .env")
    return value


def team_handle() -> str:
    team = need("TEAM_HANDLE")
    if not TEAM_RE.match(team):
        raise RuntimeError("TEAM_HANDLE must be lowercase letters, digits and dashes, 2-32 chars")
    return team


def _node_ok(version: tuple[int, int, int]) -> bool:
    # @withone/mcp refuses older patch releases (one-hackathon SKILL.md, "The CLI")
    floors = {22: (22, 23, 4), 24: (24, 22, 1), 26: (26, 12, 1)}
    return version[0] in floors and version >= floors[version[0]]


def node_bin_dir() -> str | None:
    """Directory of a Node that @withone/mcp accepts: newest nvm install, else the node on PATH."""
    found = []
    for path in glob.glob(os.path.expanduser("~/.nvm/versions/node/v*/bin/node")):
        m = re.search(r"/v(\d+)\.(\d+)\.(\d+)/bin/node$", path)
        if m:
            version = tuple(int(x) for x in m.groups())
            if _node_ok(version):
                found.append((version, os.path.dirname(path)))
    if found:
        return max(found)[1]
    node = shutil.which("node")
    if node:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout
        m = re.match(r"v(\d+)\.(\d+)\.(\d+)", out.strip())
        if m and _node_ok(tuple(int(x) for x in m.groups())):
            return os.path.dirname(node)
    return None


def one_mcp_env() -> dict[str, str]:
    """Environment for the local @withone/mcp child process."""
    env = {**os.environ, "ONE_SECRET": need("ONE_API_KEY")}
    if not env.get("ONE_CONNECTION_KEYS"):
        env.pop("ONE_CONNECTION_KEYS", None)
    node_dir = node_bin_dir()
    if node_dir:
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    return env
