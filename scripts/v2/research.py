"""Cached external research for identified resources.

GitHub repositories are corroborated with the authenticated `gh` CLI. Non-GitHub
resources are only linked after their official page is fetched and verified to
contain the resource name. All results are cached under data/research/.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

from . import config


def _run(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def gh_available() -> bool:
    if not shutil.which("gh"):
        return False
    try:
        return _run(["gh", "auth", "status"], 15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def cache_path(key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120]
    return config.RESEARCH_DIR / f"{safe}.json"


def github_research(owner: str, repo: str) -> dict | None:
    """Return public repository metadata + README, or None if it cannot be verified."""
    key = f"github_{owner}_{repo}"
    cached = cache_path(key)
    if cached.is_file():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if not gh_available():
        return None
    fields = ("nameWithOwner,url,description,homepageUrl,stargazerCount,forkCount,"
              "primaryLanguage,licenseInfo,updatedAt,isPrivate,repositoryTopics")
    try:
        proc = _run(["gh", "repo", "view", f"{owner}/{repo}", "--json", fields], 30)
        if proc.returncode:
            return None
        meta = json.loads(proc.stdout)
        expected = f"https://github.com/{owner}/{repo}".rstrip("/").casefold()
        if meta.get("isPrivate") or meta.get("url", "").rstrip("/").casefold() != expected:
            return None
        readme = _run(["gh", "api", f"repos/{owner}/{repo}/readme", "--jq", ".content"], 25)
        meta["readme_text"] = ""
        if readme.returncode == 0 and readme.stdout.strip():
            import base64
            try:
                meta["readme_text"] = base64.b64decode(
                    readme.stdout.strip(), validate=True).decode("utf-8", errors="replace")
            except (ValueError, UnicodeError):
                meta["readme_text"] = ""
        meta["owner"] = owner
        meta["repo"] = repo
        meta["url"] = expected
        meta["research_kind"] = "github"
        meta["accessed_at"] = config.now()
        cached.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def official_site_research(url: str, name: str) -> dict | None:
    """Fetch an official page and confirm it mentions the resource name."""
    if not url.startswith("https://") or any(ch.isspace() for ch in url):
        return None
    key = f"site_{urlparse(url).hostname}_{name}"
    cached = cache_path(key)
    if cached.is_file():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    tokens = [t.casefold() for t in re.findall(r"[A-Za-z0-9]+", name) if len(t) > 2]
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "DataSeek-research/2.0 (+local provenance verification)"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            final = resp.geturl()
            if resp.status != 200:
                return None
            html = resp.read(1_500_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError):
        return None
    if (urlparse(url).hostname or "").casefold() != (urlparse(final).hostname or "").casefold():
        return None
    page = unescape(re.sub(r"<[^>]+>", " ", html))
    if tokens and not any(t in page.casefold() for t in tokens):
        return None
    title = ""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    if m:
        title = normalize_title(m.group(1))
    data = {"url": url, "final_url": final, "title": title, "name": name,
            "research_kind": "official_site", "accessed_at": config.now()}
    cached.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def normalize_title(raw: str) -> str:
    return unescape(re.sub(r"\s+", " ", raw)).strip()[:300]
