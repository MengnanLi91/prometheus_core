# This file is part of Prometheus
# https://inl.gov/super-intelligence/prometheus/
#
# Copyright 2026 Battelle Energy Alliance, LLC
# All rights reserved, see NOTICE.txt for full restrictions
# https://github.com/idaholab/prometheus_core/blob/main/NOTICE.txt
#
# Licensed under the MIT License, please see LICENSE for details
# https://github.com/idaholab/prometheus_core/blob/main/LICENSE
#
# SPDX-License-Identifier: MIT

"""Public GET /.well-known/jwks.json -- the backend's own RS256 public keyset.

Lets agents verify platform-minted identity tokens without any private
material ever leaving the backend: an agent (including a third-party one)
points its JWKS_URL here instead of holding USER_IDENTITY_PUBLIC_KEY directly
(see docs/dev-docs/multi-user-setup.md). Not under /api -- JWKS is
conventionally served at the host root (RFC 7517; matches every agent's own
/.well-known/jwks.json in prometheus_api.agent.base). Unauthenticated by
design: a JWKS document is public key material only.

Serves every row in the signing_keys table, active or retired -- see
app/db/models.py::SigningKey's docstring for why retired keys aren't pruned
automatically (tokens minted before a rotation must stay verifiable).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session
from app.db.models import SigningKey
from app.openapi import TAG_IDENTITY
from app.signing_keys import jwk_for

router = APIRouter(tags=[TAG_IDENTITY])


@router.get("/.well-known/jwks.json")
async def jwks(db: AsyncSession = Depends(get_db_session)) -> dict[str, list[dict[str, Any]]]:
    rows = (await db.scalars(select(SigningKey))).all()
    return {"keys": [jwk_for(row) for row in rows]}
