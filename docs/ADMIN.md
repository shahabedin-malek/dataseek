# Administration

Implemented in `backend/api.py` (standard library only). Read endpoints are
public; every mutating endpoint requires the `X-Admin-Token` header and writes an
`audit_log` row.

## Run

```bash
.venv/bin/python backend/api.py --port 8787
# admin token: $DATASEEK_ADMIN_TOKEN or a generated backend/.admin_token (0600)
```

The admin UI is served at `/admin`.

## Endpoints

Public:
- `GET /api/stats`, `/api/categories`, `/api/search`, `/api/resources`,
  `/api/resources/<entity_id>`, `/api/screenshots`, `/api/screenshots/<task_id>`

Authenticated (header `X-Admin-Token`):
- `POST /api/admin/reprocess` `{task_id}` — re-run the v2 multi-OCR pipeline
- `POST /api/admin/resource/<entity_id>/edit` — edit allowed fields
- `POST /api/admin/resource/<entity_id>/merge` `{into}`
- `POST /api/admin/resource/<entity_id>/restore`
- `DELETE /api/admin/resource/<entity_id>` — soft delete
- `GET /api/audit` — recent audit history

## Audit and safety

- Merge and delete are non-destructive: the source entity is marked
  `is_invalid`/`deleted_at` and retained for audit and restore.
- Editable fields are allow-listed (`name`, `canonical_name`,
  `short_description`, `primary_category`, `subcategory`, `resource_type`,
  `confidence`, `license`, `developer`, `platforms`, `tags`).
- A single reprocess runs at a time (`_REPROCESS_LOCK`).
- Never expose the admin token or write API publicly without authorization.
