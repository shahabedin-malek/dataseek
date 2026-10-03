# DataSeek — Category Taxonomy

Every resource receives a **primary category** and, where evidence supports it,
**secondary categories**. Classification is rule-based (`scripts/v2/taxonomy.py`) and
records its rationale, so a category can always be explained and revised.

## Roots and subcategories

- **AI** — AI Assistants, AI Agents, AI Coding, AI Search, AI Research, AI Writing,
  AI Image, AI Video, AI Audio, AI Voice, AI OCR, AI Vision, AI Automation,
  AI Productivity, AI Developer Tools
- **Development** — IDE, Code Editor, CLI, Framework, Library, SDK, API, Database,
  DevOps, Testing, Monitoring, Infrastructure
- **Applications** — Desktop App, Mobile App, Web App, CLI App, Browser Extension,
  Developer App, Productivity App, Communication App, Security App
- **Web** — Website, SaaS, Search Engine, Documentation, Community, Marketplace, Platform
- **Data** — Database, Data Engineering, Data Science, Analytics, ETL, Search,
  Knowledge Graph, Data Visualization
- **Security** — Cybersecurity, OSINT, Privacy, Monitoring, Network, Forensics, Security Tools
- **Media** — Image, Video, Audio, Voice, Music, Content Creation
- **Documents** — OCR, PDF, Document Management, Parsing, Conversion, Knowledge Management
- **Commands** — Linux, Shell, Git, GitHub CLI, Docker, Python, Node, System Administration
- **Other** — used only when no rule matches

## Rules

1. A resource has exactly one primary category and any number of secondary categories.
2. GitHub repositories default to `Development` when no stronger keyword matches.
3. `Open Source`, `AI`, and `Documents` are commonly added as secondary categories.
4. Categories are stored structurally (`categories`, and `primary_category` /
   `secondary_categories` columns on `entities`) — never only as Markdown text.
5. The taxonomy may evolve; `docs/CATEGORY_TAXONOMY.md` and
   `.progress/CATEGORY_TAXONOMY.md` record current counts.
