# DataSeek — OCR Architecture (v2_multi_ocr)

The legacy single-engine Tesseract/PSM-6 pass is retained only as `legacy/` evidence.
All current knowledge is produced by the generation `v2_multi_ocr`.

## Pipeline

```
IMAGE (read-only, immutable)
  ↓ quality analysis            preprocess.analyze_quality
  ↓ multi-OCR (escalation)      engines.run_rapidocr + engines.run_tesseract
  ↓ preprocessing variant       preprocess.variants (only when primary is weak)
  ↓ (vision layer disabled by policy; OCR-only)
  ↓ consensus reconciliation    consensus.reconcile
  ↓ URL / entity extraction     consensus.extract_urls, github_candidates, vision_product_name
  ↓ web research                research.github_research / official_site_research
  ↓ classification              taxonomy.classify
  ↓ entity upsert               pipeline.resolve_entity
  ↓ Markdown + OCR cache        write_image_markdown, write_ocr_cache
  ↓ database checkpoint         tasks / ocr_runs / ocr_regions / ocr_consensus / urls / evidence
```

## Escalation ladder

| Level | Action | Trigger |
|---|---|---|
| 1 | RapidOCR on original | always |
| 2 | Tesseract on original (independent witness) | always |
| 3 | RapidOCR on CLAHE/grayscale variant | primary confidence < 72 **or** < 80 chars, and image is dark/low-contrast/blurry |
| 5 | Vision model reading | **disabled by project policy** (Ollama not permitted) |
| 6 | GitHub repository corroboration | a `owner/repo` candidate is found |

Level 4 (official-site verification) is applied for non-GitHub resources when a
non-aggregator domain is present and the page's own metadata confirms the name.

## Consensus rules

- Raw output from **every** engine is preserved verbatim (`data/ocr/<task>/<engine>_*.json`,
  plus `ocr_runs` / `ocr_regions` rows). Majority voting never silently replaces evidence.
- `final_text` is the highest-confidence *structured OCR* output (not the vision text).
- `agreement` is computed between the strongest engine and any credible witness
  (confidence ≥ 55). A single strong witness is not flagged as "conflicting".
- Statuses: `OCR_GOOD`, `OCR_PARTIAL`, `OCR_CONFLICTING`, `OCR_LOW_CONFIDENCE`,
  `OCR_NEEDS_VISION`, `OCR_UNREADABLE`.

## Evidence labelling

Every stored claim carries a label: `SCREENSHOT FACT`, `OCR EVIDENCE`, `VISION EVIDENCE`,
`WEB VERIFIED`, `OFFICIAL SOURCE`, `AI INFERENCE`, `UNCERTAIN`, `CONFLICTING`. AI/vision
inference is never presented as verified fact.

## Quality levels

`0` unreadable · `1` OCR extracted · `2` OCR + visual verification · `3` resource
identified · `4` resource web verified · `5` rich researched record · `6` verified +
multiple evidence sources.

## Resilience

- The source directory is read-only and never modified; variants live under
  `data/preprocess/<task>/` and are deleted after use.
- SHA-256 is re-checked before OCR; a changed source fails the task rather than
  producing wrong knowledge.
- Failures are recorded in `processing_errors` and `.progress/ERRORS.md`; a task left
  in `OCR_PROCESSING` is simply re-processed on the next batch run.
- The vision layer is disabled by project policy (Ollama is not permitted); the
  pipeline is OCR-only, so no image is sent to any model.
- Every task commits its own row set, so a crash loses at most one image.
