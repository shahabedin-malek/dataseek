# DataSeek Progress

- Updated: 2026-10-03T21:20:54+00:00
- Processing version: `v2_multi_ocr`
- Source: `/mnt/private-ai-data/Screenshot ` (immutable, trailing-space path)
- Screenshots: 907 · v2 processed: 213 · pending: 694
- Unique resources: 83
- Categories represented: 7
- GitHub repositories: 82
- AI tools: 37
- URLs discovered: 274
- Open errors: 0
- OCR engine status counts: {'OCR_GOOD': 181, 'NONE': 697, 'OCR_CONFLICTING': 14, 'OCR_NEEDS_VISION': 15}
- Quality level distribution: {0: 697, 1: 61, 2: 96, 6: 53}

## Engines
- RapidOCR (PP-OCRv6 ONNX, CPU) — primary, high confidence
- Tesseract 5.5 (TSV confidences) — secondary witness
- Vision (qwen3.5:4b via Ollama) — optional, LAN service, used when reachable

## Remaining
- Finish v2 OCR pass over all screenshots.
- Apply vision layer when the Ollama service is reachable.
- Re-run research for unresolved entities.