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


def norm_host(raw: str | None) -> str:
    """Canonical host for comparison; strips a leading www. so redirects match."""
    host = (raw or "").casefold()
    return host[4:] if host.startswith("www.") else host


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
                # GitHub returns the README base64 with embedded newlines; strip
                # all whitespace before decoding or validate=True rejects it.
                blob = "".join(readme.stdout.split())
                meta["readme_text"] = base64.b64decode(
                    blob, validate=True).decode("utf-8", errors="replace")
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


OG_SITE_RE = re.compile(
    r"(?is)<meta[^>]+(?:property|name)=[\"']og:site_name[\"'][^>]+content=[\"']([^\"']+)[\"']")
TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")


def _page_name(html: str, fallback_host: str) -> str:
    """Derive a human resource name from the page's own metadata."""
    m = OG_SITE_RE.search(html)
    if m:
        candidate = normalize_title(unescape(m.group(1)))
        if 1 < len(candidate) <= 120:
            return candidate
    title = ""
    m = TITLE_RE.search(html)
    if m:
        title = normalize_title(m.group(1))
    if not title:
        return norm_host(fallback_host)
    # Prefer the segment that matches the site's own domain label; that is the
    # brand, whereas the other segments are usually taglines.
    label = _host_label(fallback_host)
    segments = [seg.strip(" .") for seg in re.split(r"\s[|·—–]\s", title) if seg.strip(" .")]
    if label:
        for seg in segments:
            if label in seg.casefold().replace(" ", ""):
                return seg[:120]
    # Otherwise only accept a short, brand-like title; a long tagline is not a
    # name, so fall back to the domain itself (which is verifiable).
    first = segments[0] if segments else title
    if first and len(first) <= 30 and len(first.split()) <= 4:
        return first[:120]
    return norm_host(fallback_host)


def _host_label(host: str) -> str:
    parts = (host or "").split(".")
    return parts[-2] if len(parts) >= 2 else (host or "")


def official_site_research(url: str, name: str | None = None) -> dict | None:
    """Fetch a page that the screenshot itself points at and confirm identity.

    When `name` is supplied the page must mention it. When it is not, the
    resource name is taken from the page's own metadata (og:site_name/title), so
    the identity comes from the cited source rather than an inference.
    """
    if not url.startswith("https://") or any(ch.isspace() for ch in url):
        return None
    host = (urlparse(url).hostname or "").casefold()
    key = f"site_{host}_{name or 'auto'}"
    cached = cache_path(key)
    if cached.is_file():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    tokens = [t.casefold() for t in re.findall(r"[A-Za-z0-9]+", name or "") if len(t) > 2]
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
    if norm_host(host) != norm_host(urlparse(final).hostname):
        return None
    page = unescape(re.sub(r"<[^>]+>", " ", html))
    if tokens and not any(t in page.casefold() for t in tokens):
        return None
    title = ""
    m = TITLE_RE.search(html)
    if m:
        title = normalize_title(m.group(1))
    resolved = name or _page_name(html, host)
    if not resolved:
        return None
    data = {"url": url, "final_url": final, "title": title, "name": resolved,
            "research_kind": "official_site", "name_source": "ocr_url" if name else "page_metadata",
            "accessed_at": config.now()}
    cached.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def normalize_title(raw: str) -> str:
    return unescape(re.sub(r"\s+", " ", raw)).strip()[:300]
