# DataSeek Progress

- Updated: 2026-10-03T20:44:13+00:00
- Processing version: `v2_multi_ocr`
- Source: `/mnt/private-ai-data/Screenshot ` (immutable, trailing-space path)
- Screenshots: 907 · v2 processed: 61 · pending: 846
- Unique resources: 67
- Categories represented: 23
- GitHub repositories: 66
- AI tools: 1
- URLs discovered: 40
- Open errors: 0
- OCR engine status counts: {'OCR_CONFLICTING': 19, 'OCR_GOOD': 38, 'NONE': 849, 'OCR_NEEDS_VISION': 1}
- Quality level distribution: {0: 849, 1: 1, 2: 27, 3: 23, 5: 2, 6: 5}

## Engines
- RapidOCR (PP-OCRv6 ONNX, CPU) — primary, high confidence
- Tesseract 5.5 (TSV confidences) — secondary witness
- Vision (qwen3.5:4b via Ollama) — optional, LAN service, used when reachable

## Remaining
- Finish v2 OCR pass over all screenshots.
- Apply vision layer when the Ollama service is reachable.
- Re-run research for unresolved entities.