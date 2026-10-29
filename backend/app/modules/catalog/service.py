"""Public wallpaper catalog: categories, Discover sections, feeds and details."""

from google.cloud.firestore_v1.async_query import AsyncQuery
from google.cloud.firestore_v1.base_query import FieldFilter

from app.container import Container
from app.core.errors import ProblemError
from app.db.documents import CategoryDoc, WallpaperDoc, WallpaperStatus
from app.db.firestore import CATEGORIES, WALLPAPERS, query_all


class CatalogService:
    def __init__(self, container: Container) -> None:
        self.c = container
        self.db = container.db

    def _published(self) -> AsyncQuery:
        query: AsyncQuery = self.db.collection(WALLPAPERS).where(
            filter=FieldFilter("status", "==", WallpaperStatus.PUBLISHED.value)
        )
        return query

    async def categories(self) -> list[CategoryDoc]:
        query = self.db.collection(CATEGORIES).where(filter=FieldFilter("active", "==", True))
        cats = [CategoryDoc.from_doc(s.to_dict() or {}) for s in await query_all(query)]
        return sorted((c for c in cats if c.wallpaper_count > 0), key=lambda c: c.sort_order)

    async def latest(
        self, *, limit: int, category: str | None = None, before: str | None = None
    ) -> list[WallpaperDoc]:
        query = self._published()
        if category is not None:
            query = query.where(filter=FieldFilter("category", "==", category))
        query = query.order_by("published_at", direction="DESCENDING").order_by(
            "id", direction="DESCENDING"
        )
        if before is not None:
            cursor = await self.db.collection(WALLPAPERS).document(before).get()
            if not cursor.exists:
                raise ProblemError(422, "invalid_cursor", "Invalid pagination cursor.")
            query = query.start_after(cursor)
        return [
            WallpaperDoc.from_doc(s.to_dict() or {}) for s in await query_all(query.limit(limit))
        ]

    async def featured(self, *, limit: int) -> list[WallpaperDoc]:
        query = (
            self._published()
            .where(filter=FieldFilter("featured", "==", True))
            .order_by("published_at", direction="DESCENDING")
            .limit(limit)
        )
        return [WallpaperDoc.from_doc(s.to_dict() or {}) for s in await query_all(query)]

    async def get(self, wallpaper_id: str) -> WallpaperDoc:
        snap = await self.db.collection(WALLPAPERS).document(wallpaper_id).get()
        wallpaper = WallpaperDoc.from_doc(snap.to_dict() or {}) if snap.exists else None
        if wallpaper is None or wallpaper.status != WallpaperStatus.PUBLISHED:
            raise ProblemError(404, "not_found", "Wallpaper not found.")
        return wallpaper
