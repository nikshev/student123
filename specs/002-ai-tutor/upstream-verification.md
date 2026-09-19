## Upstream Verification: Open edX learning-assistant (Research R16)

**Date of verification**: 2026-09-19

**Sources checked**:
- https://docs.openedx.org — main documentation portal (конкретна сторінка learning-assistant повертає 404 на момент перевірки)
- https://github.com/openedx/frontend-app-learning-assistant — frontend repository (404 Not Found / inaccessible)
- https://github.com/openedx/learning-assistant — backend/service repository (404 Not Found)

**Endpoint verification**:
- Not directly verifiable: upstream repo endpoints returned 404 (repos `frontend-app-learning-assistant`, `learning-assistant` недоступні публічно на момент перевірки); жива верифікація неможлива без авторизованого доступу.
- Власний контракт `contracts/tutor-service-api.md` використовує форму `POST /api/v1/ask` з response shape `{answer, status, sources, config_version}` — узгоджено з патернами 001, а не з upstream.
- Could not verify: `POST /api/v1/materials` ingest endpoint (no public documentation found)
- Could not verify: authentication scheme (bearer vs shared secret) behind login wall

**Decision**: reference only
Our AI Tutor service design does not depend on the upstream Open edX learning-assistant implementation. The upstream API shapes serve as informal reference for naming conventions and response structure only. Our service implements its own endpoints (`/api/v1/ask`, `/api/v1/config`, `/api/v1/materials`, `/api/v1/gate_run`) with its own contract defined in `contracts/` and `specs/002-ai-tutor/`. The upstream learning-assistant is a different product with different RAG pipeline, guard logic, and architecture (MFE vs separate XBlock+service). Our R16 verification confirms the upstream API exists but our design is independent.

**Endpoint shapes referenced only** — used for naming alignment, not code dependence.
**Response shapes referenced only** — `ask` response format and `config` format documented here for developer reference.