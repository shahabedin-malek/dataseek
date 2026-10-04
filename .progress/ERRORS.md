# Errors and Blockers

- Open errors: 0
- none

## Known environment issues
- The source directory path ends with a literal space (`/mnt/private-ai-data/Screenshot `).
- The vision layer is disabled by project policy (Ollama is not permitted); the pipeline is OCR-only.
- Vercel production (`https://dataseek-gules.vercel.app`) returns NOT_FOUND; the project must be relinked/redeployed (`npx vercel --cwd site --prod`) with credentials. The static bundle in `site/` is built and ready, and the GitHub repository is up to date.