# daemon/routes/digest.py
"""S32-2/3/4/5 — digest triggers and the skills export."""

import asyncio
import logging

from fastapi import APIRouter, Depends

from daemon.auth import verify_api_key
from daemon.dependencies import Dependencies, get_dependencies
from daemon.helpers.responses import bad_request, server_error
from daemon.helpers.validation import _canonicalize_vault_root
from daemon.models.digest import DigestRequest, SkillsExportRequest

logger = logging.getLogger("vault-memoryd")

digest_router = APIRouter()

DIGEST_KINDS = ("daily", "weekly", "monthly")


def _vault_root(deps: Dependencies):
    try:
        root = _canonicalize_vault_root(deps.settings.vault_path)
    except Exception:
        return None, bad_request("Invalid vault path", code="INVALID_VAULT_PATH")
    if not root.is_dir():
        return None, bad_request("Vault path does not exist", code="INVALID_VAULT_PATH")
    return root, None


@digest_router.post("/digest/{kind}", status_code=201)
async def run_digest_route(
    kind: str,
    req: DigestRequest = None,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Build one digest (``daily`` / ``weekly`` / ``monthly``).

    ``monthly`` also stages skill proposals under ``_working/consolidation/``.
    The digest is written even when the summary model is unavailable.
    """
    from daemon import digest

    if kind not in DIGEST_KINDS:
        return bad_request(
            f"unknown digest kind '{kind}' (expected one of: {', '.join(DIGEST_KINDS)}) ",
            code="INVALID_DIGEST_KIND",
        )

    root, error = _vault_root(deps)
    if error:
        return error

    # ``summarise=False`` is the offline/no-model path: structure without prose.
    llm = None
    if req is None or req.summarise:
        from daemon.miner import make_default_llm

        llm = make_default_llm(getattr(deps, "settings", None))

    try:
        return await digest.run_digest(deps, root, kind, llm=llm)
    except Exception:
        logger.exception("digest run failed")
        return server_error("Digest failed", code="DIGEST_FAILED")


@digest_router.get("/digest")
async def list_digests(
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Digests already on disk, newest name first."""
    from daemon import digest

    root, error = _vault_root(deps)
    if error:
        return error

    directory = root / digest.DIGESTS_DIRNAME
    files = sorted((p.name for p in directory.glob("*.md")), reverse=True) if directory.is_dir() else []
    return {"count": len(files), "digests": files}


@digest_router.post("/skills/export", status_code=201)
async def skills_export(
    req: SkillsExportRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Publish corroborated lessons as ``skills/<theme>/SKILL.md`` bundles.

    A SKILL.md that exists without this generator's marker is never overwritten;
    that theme's export is staged under ``_working/skills/`` instead.
    """
    from daemon import digest

    root, error = _vault_root(deps)
    if error:
        return error

    try:
        result = await asyncio.to_thread(
            digest.export_skills,
            root,
            project=req.project,
            min_corroboration=req.min_corroboration,
        )
    except Exception:
        logger.exception("skills export failed")
        return server_error("Skills export failed", code="SKILLS_EXPORT_FAILED")
    return result


@digest_router.get("/skills")
async def list_skills(
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Exported skill bundles (name, path, description)."""
    from daemon import digest

    root, error = _vault_root(deps)
    if error:
        return error

    skills = []
    directory = root / digest.SKILLS_DIRNAME
    if directory.is_dir():
        for path in sorted(directory.glob("*/SKILL.md")):
            head = path.read_text(encoding="utf-8", errors="replace")[:600]
            name = path.parent.name
            description = ""
            for line in head.splitlines():
                if line.startswith("description:"):
                    description = line.partition(":")[2].strip()
                    break
            skills.append(
                {
                    "name": name,
                    "path": str(path.relative_to(root)),
                    "description": description,
                    "generated": f"generated-by: {digest.GENERATED_BY}" in head,
                }
            )
    return {"count": len(skills), "skills": skills}
