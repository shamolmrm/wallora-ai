import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../core/network/api_client.dart';

List<Map<String, Object?>> _maps(Object? value) =>
    (value as List<Object?>? ?? const []).cast<Map<String, Object?>>();

class Category {
  const Category({required this.slug, required this.name, required this.count});

  factory Category.fromJson(Map<String, Object?> json) => Category(
    slug: json['slug']! as String,
    name: json['name']! as String,
    count: json['wallpaper_count']! as int,
  );

  final String slug;
  final String name;
  final int count;
}

class Wallpaper {
  const Wallpaper({
    required this.id,
    required this.title,
    required this.category,
    required this.width,
    required this.height,
    required this.thumbUrl,
    required this.url,
    required this.isPremium,
    required this.isAiGenerated,
  });

  factory Wallpaper.fromJson(Map<String, Object?> json) => Wallpaper(
    id: json['id']! as String,
    title: json['title']! as String,
    category: json['category']! as String,
    width: json['width']! as int,
    height: json['height']! as int,
    thumbUrl: json['thumb_url']! as String,
    url: json['url']! as String,
    isPremium: json['is_premium']! as bool,
    isAiGenerated: json['source_type'] == 'ai_inhouse',
  );

  final String id;
  final String title;
  final String category;
  final int width;
  final int height;
  final String thumbUrl;
  final String url;
  final bool isPremium;
  final bool isAiGenerated;
}

class DiscoverData {
  const DiscoverData({
    required this.featured,
    required this.categories,
    required this.latest,
  });

  factory DiscoverData.fromJson(Map<String, Object?> json) => DiscoverData(
    featured: _maps(json['featured']).map(Wallpaper.fromJson).toList(),
    categories: _maps(json['categories']).map(Category.fromJson).toList(),
    latest: _maps(json['latest']).map(Wallpaper.fromJson).toList(),
  );

  final List<Wallpaper> featured;
  final List<Category> categories;
  final List<Wallpaper> latest;

  bool get isEmpty => featured.isEmpty && latest.isEmpty;
}

class CatalogRepository {
  CatalogRepository(this._api);

  final ApiClient _api;

  Future<DiscoverData> discover() async =>
      DiscoverData.fromJson(await _api.getJson('/v1/discover'));

  Future<List<Wallpaper>> byCategory(String slug, {int limit = 50}) async {
    final page = await _api.getJson(
      '/v1/wallpapers',
      query: {'category': slug, 'limit': limit},
    );
    return _maps(page['items']).map(Wallpaper.fromJson).toList();
  }

  Future<Wallpaper> wallpaper(String id) async =>
      Wallpaper.fromJson(await _api.getJson('/v1/wallpapers/$id'));
}

final catalogRepositoryProvider = Provider<CatalogRepository>(
  (ref) => CatalogRepository(ref.watch(apiClientProvider)),
);

final discoverProvider = FutureProvider<DiscoverData>(
  (ref) => ref.watch(catalogRepositoryProvider).discover(),
);

final categoryWallpapersProvider =
    FutureProvider.family<List<Wallpaper>, String>(
      (ref, slug) => ref.watch(catalogRepositoryProvider).byCategory(slug),
    );

final wallpaperProvider = FutureProvider.family<Wallpaper, String>(
  (ref, id) => ref.watch(catalogRepositoryProvider).wallpaper(id),
);
