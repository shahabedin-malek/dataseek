# Decisions

- Originals at `/mnt/private-ai-data/Screenshot ` are immutable; all variants live under data/preprocess.
- v2 generation `v2_multi_ocr` supersedes the legacy Tesseract-only pass; legacy outputs retained.
- RapidOCR is the primary engine (highest confidence on this no-AVX2 CPU); Tesseract is a second witness.
- Vision reconciliation never lets majority voting alone decide; raw engine output is always preserved.
- A resource is only created from a candidate product name when OCR and vision independently agree; it is marked LOW/NEEDS_RESEARCH, never verified.
- GitHub identities require the public repo metadata + README to match; other sites require the official page to contain the resource name.
- Unreviewed data default to REVIEW_REQUIRED / private.
- A resource may also be resolved from a URL that is visibly present in the screenshot: the page is fetched and its own metadata (og:site_name/title) supplies the name (`name_source=page_metadata`), so identity is cited, not inferred.
- Tasks stamped with a processing version but left in OCR_PROCESSING are treated as incomplete and re-queued, because they have no artifacts.
- urls.entity_id is backfilled/upserted once a screenshot's resource is resolved.
- A GitHub owner rename is never accepted silently: the returned canonical URL must match the visible one, so truncated OCR cannot resolve to an unrelated repository.
- resolve_cached also treats a github.com/owner/repo URL already extracted from the media as candidate evidence (a cited link, not an inference).
- Rows with no verifiable identity stay unresolved (level 1-2) rather than guessed.
- The vision layer is disabled by project policy (Ollama is not permitted); OCR-only is terminal.
- Records flagged NEEDS_VISION when vision was still wired up were reclassified to OCR_REVIEW (held for review) rather than left permanently blocked.
- GitHub-canonical candidates are corroborated with repository metadata + README via the authenticated gh CLI; README features and the license fall back to the human name when no SPDX id exists.