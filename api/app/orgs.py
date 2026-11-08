"""Which org a user belongs to.

Org membership comes from Google's `hd` claim (hosted domain), which Google sets only for Google Workspace
accounts whose domain is verified. The email suffix alone is NOT trusted: anyone can create a consumer Google
account with an @acme.com address, and that person must not join the "acme" org and see its shared examples.

    hd = "acme.com"  -> org "acme" (shared with every acme.com Workspace user)
    no hd            -> personal org "user-<shortid>" (gmail.com, googlemail.com, any consumer account)
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Org

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 48) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:max_len].strip("-") or "org"


async def _unique_slug(db: AsyncSession, base: str, fallback: str) -> str:
    """'acme' if free, else 'acme-co-uk' (whole domain), else 'acme-co-uk-2', ..."""
    for candidate in (base, fallback, *(f"{fallback}-{i}" for i in range(2, 100))):
        if not (await db.execute(select(Org.id).where(Org.slug == candidate))).first():
            return candidate
    raise RuntimeError(f"no free org slug for {fallback}")


async def org_for_domain(db: AsyncSession, hosted_domain: str) -> Org:
    domain = hosted_domain.lower()
    org = (await db.execute(select(Org).where(Org.email_domain == domain))).scalar_one_or_none()
    if org:
        return org
    slug = await _unique_slug(db, slugify(domain.split(".")[0]), slugify(domain))
    org = Org(slug=slug, display_name=domain.split(".")[0].capitalize(), email_domain=domain, is_personal=False)
    db.add(org)
    await db.flush()
    return org


async def personal_org(db: AsyncSession, short_id: str, display_name: str) -> Org:
    org = Org(slug=f"user-{short_id}", display_name=display_name, email_domain=None, is_personal=True)
    db.add(org)
    await db.flush()
    return org
