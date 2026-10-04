# Data Model

Core relations:

```
tasks (screenshot) ──1:1── ocr_consensus        reconciled reading
tasks ──1:N── ocr_runs ──1:N── ocr_regions      raw multi-engine evidence
tasks ──1:N── vision_runs                       optional vision readings
tasks ──N:1── entities                          resolved resource (or NULL)
entities ──1:N── entity_images ──N:1── tasks    provenance (many-to-many)
entities ──1:N── entity_urls / urls             canonical vs OCR-observed URLs
entities ──1:N── features / technologies / tags
entities ──1:N── evidence                        labelled evidence entries
entities ──1:N── relationships                  evidence-backed links
tasks   ──1:N── duplicate_links                 exact/near duplicate pairs
*        ──1:N── audit_log                      mutation history
```

## Identity rules

- **Screenshot** identity is the stable `IMG-nnnn` task id, assigned once from the
  sorted relative source path. It never changes.
- **Resource** identity is the `entities.entity_id`. A new id is created only when
  no existing entity matches on canonical name or name. Additional screenshots of
  an existing resource attach via `entity_images` and increment `source_count`.
- **Duplicate screenshots** keep their own row and OCR evidence; `duplicate_of` /
  `duplicate_links` record the relationship. Duplicates are evidence, never
  deleted.

## Quality levels (`tasks.quality_level`)

0 unreadable/unresolved · 1 OCR extracted · 2 OCR + visual verification ·
3 resource identified · 4 resource web verified · 5 rich researched record ·
6 verified resource + multiple evidence sources.

## Privacy

Every task defaults to `visibility = REVIEW_REQUIRED`. Original screenshots and
OCR caches are local-only and are never copied into the deployable bundle.
