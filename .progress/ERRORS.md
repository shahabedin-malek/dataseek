# Errors and Blockers

- Open errors: 0
- none

## Known environment issues
- The source directory path ends with a literal space (`/mnt/private-ai-data/Screenshot `).
- The vision layer is disabled by project policy (Ollama is not permitted); the pipeline is OCR-only.
- Vercel production is live at `https://dataseek-gules.vercel.app`. The project Root Directory must stay set to `site`; if it is reset to the repository root the alias returns NOT_FOUND while `/site/index.html` still resolves.