import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/design_system/tokens.dart';
import '../../core/design_system/widgets.dart';
import 'data/catalog.dart';

class DiscoverScreen extends ConsumerWidget {
  const DiscoverScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final discover = ref.watch(discoverProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Discover')),
      body: RefreshIndicator(
        onRefresh: () => ref.refresh(discoverProvider.future),
        child: discover.when(
          loading: () => const LoadingView(label: 'Loading wallpapers'),
          error: (error, _) => ErrorView(
            error: error,
            onRetry: () => ref.invalidate(discoverProvider),
          ),
          data: (data) => data.isEmpty
              ? ListView(
                  children: [
                    const SizedBox(height: 120),
                    StatusMessage(
                      icon: Icons.explore_outlined,
                      title: 'No wallpapers yet',
                      message:
                          'New collections are on the way. Meanwhile, '
                          'create your own in the Studio.',
                      actionLabel: 'Open Studio',
                      onAction: () => context.go('/studio'),
                    ),
                  ],
                )
              : _DiscoverBody(data: data),
        ),
      ),
    );
  }
}

class _DiscoverBody extends StatelessWidget {
  const _DiscoverBody({required this.data});

  final DiscoverData data;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return CustomScrollView(
      slivers: [
        if (data.featured.isNotEmpty)
          SliverToBoxAdapter(
            child: SizedBox(
              height: 380,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: Spacing.lg),
                itemCount: data.featured.length,
                separatorBuilder: (context, index) =>
                    const SizedBox(width: Spacing.md),
                itemBuilder: (context, index) => SizedBox(
                  width: 214,
                  child: WallpaperTile(
                    wallpaper: data.featured[index],
                    radius: Radii.lg,
                  ),
                ),
              ),
            ),
          ),
        if (data.categories.isNotEmpty)
          SliverToBoxAdapter(
            child: SizedBox(
              height: 64,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.all(Spacing.lg).copyWith(bottom: 0),
                itemCount: data.categories.length,
                separatorBuilder: (context, index) =>
                    const SizedBox(width: Spacing.sm),
                itemBuilder: (context, index) {
                  final category = data.categories[index];
                  return ActionChip(
                    label: Text(category.name),
                    onPressed: () => context.push(
                      '/category/${category.slug}?name=${Uri.encodeComponent(category.name)}',
                    ),
                  );
                },
              ),
            ),
          ),
        SliverPadding(
          padding: const EdgeInsets.fromLTRB(
            Spacing.lg,
            Spacing.xl,
            Spacing.lg,
            Spacing.sm,
          ),
          sliver: SliverToBoxAdapter(
            child: Text('Latest', style: theme.textTheme.titleMedium),
          ),
        ),
        SliverPadding(
          padding: const EdgeInsets.symmetric(horizontal: Spacing.lg),
          sliver: WallpaperGrid.sliver(data.latest),
        ),
        const SliverToBoxAdapter(child: SizedBox(height: Spacing.xl)),
      ],
    );
  }
}

/// Grid of wallpaper thumbnails, as a sliver or a standalone scroll view.
abstract final class WallpaperGrid {
  static const _delegate = SliverGridDelegateWithFixedCrossAxisCount(
    crossAxisCount: 3,
    mainAxisSpacing: Spacing.sm,
    crossAxisSpacing: Spacing.sm,
    childAspectRatio: 9 / 19.5,
  );

  static Widget sliver(List<Wallpaper> wallpapers) => SliverGrid.builder(
    gridDelegate: _delegate,
    itemCount: wallpapers.length,
    itemBuilder: (context, index) =>
        WallpaperTile(wallpaper: wallpapers[index]),
  );

  static Widget box(List<Wallpaper> wallpapers) => GridView.builder(
    padding: const EdgeInsets.all(Spacing.lg),
    gridDelegate: _delegate,
    itemCount: wallpapers.length,
    itemBuilder: (context, index) =>
        WallpaperTile(wallpaper: wallpapers[index]),
  );
}

class WallpaperTile extends StatelessWidget {
  const WallpaperTile({
    super.key,
    required this.wallpaper,
    this.radius = Radii.sm,
  });

  final Wallpaper wallpaper;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Semantics(
      button: true,
      label: wallpaper.title,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(radius),
        child: Material(
          color: scheme.surfaceContainerHigh,
          child: InkWell(
            onTap: () => context.push('/wallpaper/${wallpaper.id}'),
            child: Image.network(
              wallpaper.thumbUrl,
              fit: BoxFit.cover,
              width: double.infinity,
              height: double.infinity,
              errorBuilder: (context, error, stack) =>
                  const Icon(Icons.broken_image_outlined),
            ),
          ),
        ),
      ),
    );
  }
}

class CategoryScreen extends ConsumerWidget {
  const CategoryScreen({super.key, required this.slug, required this.name});

  final String slug;
  final String name;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wallpapers = ref.watch(categoryWallpapersProvider(slug));
    return Scaffold(
      appBar: AppBar(title: Text(name)),
      body: wallpapers.when(
        loading: () => const LoadingView(),
        error: (error, _) => ErrorView(
          error: error,
          onRetry: () => ref.invalidate(categoryWallpapersProvider(slug)),
        ),
        data: (items) => items.isEmpty
            ? const StatusMessage(
                icon: Icons.image_search_rounded,
                title: 'Nothing here yet',
              )
            : WallpaperGrid.box(items),
      ),
    );
  }
}

class WallpaperScreen extends ConsumerWidget {
  const WallpaperScreen({super.key, required this.id});

  final String id;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final wallpaper = ref.watch(wallpaperProvider(id));
    return Scaffold(
      backgroundColor: Colors.black,
      extendBodyBehindAppBar: true,
      appBar: AppBar(
        backgroundColor: Colors.transparent,
        foregroundColor: Colors.white,
      ),
      body: wallpaper.when(
        loading: () => const LoadingView(),
        error: (error, _) => ErrorView(
          error: error,
          onRetry: () => ref.invalidate(wallpaperProvider(id)),
        ),
        data: (w) => Stack(
          fit: StackFit.expand,
          children: [
            Image.network(
              w.url,
              fit: BoxFit.cover,
              semanticLabel: w.title,
              loadingBuilder: (context, child, progress) =>
                  progress == null ? child : const LoadingView(),
              errorBuilder: (context, error, stack) => const StatusMessage(
                icon: Icons.broken_image_outlined,
                title: "Couldn't load the wallpaper",
              ),
            ),
            Positioned(
              left: 0,
              right: 0,
              bottom: 0,
              child: DecoratedBox(
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [Colors.transparent, Colors.black87],
                  ),
                ),
                child: SafeArea(
                  top: false,
                  child: Padding(
                    padding: const EdgeInsets.all(Spacing.xl),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          w.title,
                          style: Theme.of(
                            context,
                          ).textTheme.titleLarge?.copyWith(color: Colors.white),
                        ),
                        const SizedBox(height: Spacing.xs),
                        Text(
                          '${w.width} × ${w.height}'
                          '${w.isAiGenerated ? ' · AI-generated' : ''}',
                          style: const TextStyle(color: Colors.white70),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class SearchScreen extends StatelessWidget {
  const SearchScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Search')),
      body: const StatusMessage(
        icon: Icons.search_rounded,
        title: 'Search is coming',
        message: 'Meanwhile, browse categories on the Discover tab.',
      ),
    );
  }
}
