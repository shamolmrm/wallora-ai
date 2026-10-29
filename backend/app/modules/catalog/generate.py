"""Generate the in-house AI wallpaper catalog.

    py -m uv run python -m app.modules.catalog.generate --per-category 2 --dry-run
    py -m uv run python -m app.modules.catalog.generate --per-category 2 --max-usd 3 --confirm-spend

Spending safeguards:
* With a paid provider (``IMAGE_PROVIDER=openai``) the run refuses to start
  unless ``--confirm-spend`` and ``--max-usd`` are both given.
* Before every image the conservative cost estimate is checked against the
  remaining budget; the run stops once the next image could exceed it.
* Wallpaper IDs are derived from (category, prompt): re-running skips what
  already exists, so an interrupted run never pays twice for the same image.
* Every output passes image moderation before it is published.
"""

import argparse
import asyncio
import hashlib
import logging
import sys
from dataclasses import dataclass, field

from google.cloud import firestore

from app.container import Container, build_container
from app.core.config import ImageProviderName, get_settings
from app.core.imaging import InvalidImageError, process_output
from app.db.documents import WallpaperDoc, WallpaperStatus
from app.db.firestore import CATEGORIES, WALLPAPERS
from app.modules.catalog.categories import CATEGORY_SEEDS
from app.modules.studio.catalog import ASPECTS, TIERS, cost_micros, estimate_cost_micros
from app.modules.studio.prompts import compose_prompt
from app.providers.image.base import ImageRequest, ProviderError
from app.providers.image.models import MAX_PIXELS
from app.providers.moderation.base import ModerationUnavailableError

logger = logging.getLogger("app.catalog.generate")

TIER = TIERS["standard"]
ASPECT = ASPECTS["phone"]
LICENSE = "Wallora in-house AI generation; owned by the publisher"
MAX_TITLE_CHARS = 48


@dataclass
class Report:
    generated: int = 0
    skipped_existing: int = 0
    blocked: int = 0
    failed: int = 0
    stopped_for_budget: bool = False
    spent_micros: int = 0
    planned: list[str] = field(default_factory=list)


def wallpaper_id(category: str, prompt: str) -> str:
    return "cat-" + hashlib.sha256(f"{category}\x1f{prompt}".encode()).hexdigest()[:24]


def title_for(prompt: str) -> str:
    short = prompt.split(",", maxsplit=1)[0].strip()
    return short if len(short) <= MAX_TITLE_CHARS else short[: MAX_TITLE_CHARS - 3].rstrip() + "…"


async def generate_catalog(
    container: Container,
    *,
    per_category: int,
    max_usd: float | None,
    confirm_spend: bool,
    dry_run: bool,
) -> Report:
    paid = container.settings.image_provider is not ImageProviderName.FAKE
    if paid and not dry_run and not (confirm_spend and max_usd is not None):
        raise SystemExit("Refusing to run a paid provider without --confirm-spend and --max-usd.")
    budget = round((max_usd or 0) * 1_000_000) if paid else None
    estimate = estimate_cost_micros(
        model=TIER.model,
        quality=TIER.quality,
        width=ASPECT.width,
        height=ASPECT.height,
        prompt_chars=300,
        input_images=0,
    )
    report = Report()
    db = container.db
    now = container.clock()

    for order, seed in enumerate(CATEGORY_SEEDS):
        for index, prompt in enumerate(seed.prompts[:per_category]):
            wid = wallpaper_id(seed.slug, prompt)
            ref = db.collection(WALLPAPERS).document(wid)
            if (await ref.get()).exists:
                report.skipped_existing += 1
                continue
            report.planned.append(f"{seed.slug}: {prompt}")
            if dry_run:
                continue
            if budget is not None and report.spent_micros + estimate > budget:
                report.stopped_for_budget = True
                return report
            request = ImageRequest(
                model=TIER.model,
                prompt=compose_prompt(prompt, None),
                width=ASPECT.width,
                height=ASPECT.height,
                quality=TIER.quality,
                output_format="webp",
                safety_identifier="wallora-catalog",
            )
            try:
                result = await container.image_provider.generate(request)
            except ProviderError as exc:
                report.failed += 1
                report.spent_micros += estimate if exc.billing_uncertain else 0
                logger.warning("catalog_generation_failed %s %s", wid, exc.code)
                continue
            # The fake provider is free: only count spend for paid providers.
            image_cost = (
                (cost_micros(TIER.model, result.usage) if result.usage else estimate) if paid else 0
            )
            report.spent_micros += image_cost
            try:
                output = await asyncio.to_thread(process_output, result.data, max_pixels=MAX_PIXELS)
                verdict = await container.moderation.check_image(output.data, output.mime_type)
            except (InvalidImageError, ModerationUnavailableError) as exc:
                report.failed += 1
                logger.warning("catalog_output_rejected %s %s", wid, type(exc).__name__)
                continue
            if verdict.blocked:
                report.blocked += 1
                continue
            full_key, thumb_key = f"catalog/{wid}.webp", f"catalog/{wid}_thumb.webp"
            await container.storage.put(full_key, output.data, output.mime_type)
            await container.storage.put(thumb_key, output.thumb, "image/webp")
            wallpaper = WallpaperDoc(
                id=wid,
                title=title_for(prompt),
                category=seed.slug,
                tags=[seed.slug],
                status=WallpaperStatus.PUBLISHED,
                source_type="ai_inhouse",
                license=LICENSE,
                width=output.width,
                height=output.height,
                mime_type=output.mime_type,
                storage_key=full_key,
                thumb_key=thumb_key,
                created_at=now,
                published_at=container.clock(),
                featured=index == 0,
                model=TIER.model,
                cost_usd_micros=image_cost,
            )
            await ref.set(wallpaper.to_doc())
            category_ref = db.collection(CATEGORIES).document(seed.slug)
            await category_ref.set(
                {
                    "slug": seed.slug,
                    "name": seed.name,
                    "sort_order": order,
                    "active": True,
                    "wallpaper_count": firestore.Increment(1),
                },
                merge=True,
            )
            if index == 0:
                await category_ref.set({"cover_wallpaper_id": wid}, merge=True)
            report.generated += 1
    return report


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    container = build_container(settings)
    try:
        report = await generate_catalog(
            container,
            per_category=args.per_category,
            max_usd=args.max_usd,
            confirm_spend=args.confirm_spend,
            dry_run=args.dry_run,
        )
    finally:
        await container.close()
    print(f"provider={settings.image_provider} dry_run={args.dry_run}")
    print(
        f"planned={len(report.planned)} generated={report.generated} "
        f"skipped_existing={report.skipped_existing} blocked={report.blocked} "
        f"failed={report.failed} stopped_for_budget={report.stopped_for_budget} "
        f"spent_usd={report.spent_micros / 1_000_000:.4f}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--per-category", type=int, default=2, choices=range(1, 5))
    parser.add_argument("--max-usd", type=float, default=None)
    parser.add_argument("--confirm-spend", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
