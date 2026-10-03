# Decisions

- Originals at `/mnt/private-ai-data/Screenshot ` are immutable; all variants live under data/preprocess.
- v2 generation `v2_multi_ocr` supersedes the legacy Tesseract-only pass; legacy outputs retained.
- RapidOCR is the primary engine (highest confidence on this no-AVX2 CPU); Tesseract is a second witness.
- Vision reconciliation never lets majority voting alone decide; raw engine output is always preserved.
- A resource is only created from a candidate product name when OCR and vision independently agree; it is marked LOW/NEEDS_RESEARCH, never verified.
- GitHub identities require the public repo metadata + README to match; other sites require the official page to contain the resource name.
- Unreviewed data default to REVIEW_REQUIRED / private.