"""Hierarchical category taxonomy and rule-based classification.

The taxonomy is intentionally data-driven so it can evolve; classification is
conservative and always records WHY a category was chosen.
"""
from __future__ import annotations

import re

TAXONOMY: dict[str, list[str]] = {
    "AI": ["AI Assistants", "AI Agents", "AI Coding", "AI Search", "AI Research",
           "AI Writing", "AI Image", "AI Video", "AI Audio", "AI Voice", "AI OCR",
           "AI Vision", "AI Automation", "AI Productivity", "AI Developer Tools"],
    "Development": ["IDE", "Code Editor", "CLI", "Framework", "Library", "SDK",
                    "API", "Database", "DevOps", "Testing", "Monitoring",
                    "Infrastructure"],
    "Applications": ["Desktop App", "Mobile App", "Web App", "CLI App",
                     "Browser Extension", "Developer App", "Productivity App",
                     "Communication App", "Security App"],
    "Web": ["Website", "SaaS", "Search Engine", "Documentation", "Community",
            "Marketplace", "Platform"],
    "Data": ["Database", "Data Engineering", "Data Science", "Analytics", "ETL",
             "Search", "Knowledge Graph", "Data Visualization"],
    "Security": ["Cybersecurity", "OSINT", "Privacy", "Monitoring", "Network",
                 "Forensics", "Security Tools"],
    "Media": ["Image", "Video", "Audio", "Voice", "Music", "Content Creation"],
    "Documents": ["OCR", "PDF", "Document Management", "Parsing", "Conversion",
                  "Knowledge Management"],
    "Commands": ["Linux", "Shell", "Git", "GitHub CLI", "Docker", "Python",
                 "Node", "System Administration"],
    "Other": [],
}

# keyword -> (primary root, subcategory). Order matters; first match wins.
# Put the most specific domains first so broad AI acronyms cannot shadow them.
RULES: list[tuple[str, str, str]] = [
    (r"signal intelligence|software.defined radio|\bsdr\b|rtl_?sdr|rtl_433|radio frequency|rf spectrum|spy stations|number stations", "Security", "OSINT"),
    (r"\bocr\b|optical character recognition|text recognition", "Documents", "OCR"),
    (r"pdf|document parsing|docx|invoice", "Documents", "PDF"),
    (r"voice|text.to.speech|\btts\b|speech synthesis|speech.to.text|\bstt\b", "AI", "AI Voice"),
    (r"llm|large language model|gpt|chatbot|assistant|chatgpt", "AI", "AI Assistants"),
    (r"\bagent\b|agentic|autonomous agent", "AI", "AI Agents"),
    (r"copilot|coding assistant|code generation", "AI", "AI Coding"),
    (r"\brag\b|retrieval.augmented", "AI", "AI Research"),
    (r"image generation|text.to.image|stable diffusion|diffusion|midjourney|dall-?e|\bflux\b|generate.{0,25}(image|photo|art)|cinematic", "AI", "AI Image"),
    (r"video generation|text.to.video|generate.{0,25}video|\bsora\b|\bveo\b|runway|kling", "AI", "AI Video"),
    (r"\bmcp\b|model context protocol", "AI", "AI Developer Tools"),
    (r"browser extension|chrome extension|firefox add.on", "Applications", "Browser Extension"),
    (r"android|ios|mobile app|apk", "Applications", "Mobile App"),
    (r"self.hosted|docker compose|dockerized", "Development", "DevOps"),
    (r"\bcli\b|command.line", "Development", "CLI"),
    (r"framework", "Development", "Framework"),
    (r"library|\bsdk\b", "Development", "Library"),
    (r"\bapi\b", "Development", "API"),
    (r"database|sqlite|postgres|mysql|mongodb", "Data", "Database"),
    (r"osint|reconnaissance|cyber.?threat intelligence", "Security", "OSINT"),
    (r"security|vulnerability|pentest|exploit|malware", "Security", "Security Tools"),
    (r"privacy|encryption|anonym", "Security", "Privacy"),
    (r"search engine|full.text search", "Web", "Search Engine"),
    (r"saas|subscription|pricing plan", "Web", "SaaS"),
    (r"knowledge management|notes|wiki", "Documents", "Knowledge Management"),
]


def classify(text: str, screenshot_type: str | None = None,
             resource_type: str | None = None) -> dict:
    """Return {primary_category, subcategory, secondary_categories, rationale}."""
    lowered = (text or "").lower()
    primary, sub, reason = None, None, []
    for pattern, root, subcat in RULES:
        if re.search(pattern, lowered):
            primary, sub = root, subcat
            reason.append(f"matched /{pattern}/ -> {root} > {subcat}")
            break

    if resource_type == "Repository" and primary is None:
        primary, sub = "Development", None
        reason.append("GitHub repository with no stronger keyword match")
    if primary is None:
        if screenshot_type == "GITHUB":
            primary, sub = "Development", None
            reason.append("screenshot type GITHUB")
        else:
            primary, sub = "Other", None
            reason.append("no rule matched")

    secondary: list[str] = []
    if resource_type == "Repository" and "Development" not in (primary,):
        secondary.append("Development")
    if re.search(r"\bai\b|llm|machine learning|neural", lowered) and primary != "AI":
        secondary.append("AI")
    if "Documents" not in (primary,) and re.search(r"ocr|pdf|document", lowered):
        secondary.append("Documents")
    if re.search(r"open.source|github", lowered) and "Open Source" not in secondary:
        secondary.append("Open Source")
    return {
        "primary_category": primary,
        "subcategory": sub,
        "secondary_categories": sorted(set(secondary)),
        "rationale": "; ".join(reason),
    }


def valid_subcategory(root: str, sub: str | None) -> bool:
    return sub is None or sub in TAXONOMY.get(root, [])
