import asyncio
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import ContainerDep
from app.container import Container
from app.db.documents import WallpaperDoc
from app.modules.catalog.service import CatalogService

router = APIRouter(prefix="/v1", tags=["catalog"])


class CategoryOut(BaseModel):
    slug: str
    name: str
    wallpaper_count: int


class WallpaperOut(BaseModel):
    id: str
    title: str
    category: str
    tags: list[str]
    width: int
    height: int
    is_premium: bool
    source_type: str
    thumb_url: str
    url: str
    url_expires_in: int


class WallpaperPage(BaseModel):
    items: list[WallpaperOut]
    next_cursor: str | None


class DiscoverOut(BaseModel):
    featured: list[WallpaperOut]
    categories: list[CategoryOut]
    latest: list[WallpaperOut]


async def _out(container: Container, wallpapers: list[WallpaperDoc]) -> list[WallpaperOut]:
    ttl = container.settings.signed_url_ttl_seconds

    async def one(w: WallpaperDoc) -> WallpaperOut:
        thumb, full = await asyncio.gather(
            container.storage.signed_url(w.thumb_key, ttl),
            container.storage.signed_url(w.storage_key, ttl),
        )
        return WallpaperOut(
            id=w.id,
            title=w.title,
            category=w.category,
            tags=w.tags,
            width=w.width,
            height=w.height,
            is_premium=w.is_premium,
            source_type=w.source_type,
            thumb_url=thumb,
            url=full,
            url_expires_in=ttl,
        )

    return list(await asyncio.gather(*(one(w) for w in wallpapers)))


@router.get("/discover", response_model=DiscoverOut)
async def discover(container: ContainerDep) -> DiscoverOut:
    service = CatalogService(container)
    featured, categories, latest = await asyncio.gather(
        service.featured(limit=10), service.categories(), service.latest(limit=24)
    )
    return DiscoverOut(
        featured=await _out(container, featured),
        categories=[
            CategoryOut(slug=c.slug, name=c.name, wallpaper_count=c.wallpaper_count)
            for c in categories
        ],
        latest=await _out(container, latest),
    )


@router.get("/categories", response_model=list[CategoryOut])
async def categories(container: ContainerDep) -> list[CategoryOut]:
    return [
        CategoryOut(slug=c.slug, name=c.name, wallpaper_count=c.wallpaper_count)
        for c in await CatalogService(container).categories()
    ]


@router.get("/wallpapers", response_model=WallpaperPage)
async def wallpapers(
    container: ContainerDep,
    category: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> WallpaperPage:
    items = await CatalogService(container).latest(limit=limit, category=category, before=cursor)
    return WallpaperPage(
        items=await _out(container, items),
        next_cursor=items[-1].id if len(items) == limit else None,
    )


@router.get("/wallpapers/{wallpaper_id}", response_model=WallpaperOut)
async def wallpaper(wallpaper_id: str, container: ContainerDep) -> WallpaperOut:
    return (await _out(container, [await CatalogService(container).get(wallpaper_id)]))[0]
