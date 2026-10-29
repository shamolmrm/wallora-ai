import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/providers.dart';
import '../../core/design_system/tokens.dart';
import '../../core/design_system/widgets.dart';
import '../studio/data/studio_models.dart';

class LibraryScreen extends ConsumerWidget {
  const LibraryScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final history = ref.watch(historyProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Library')),
      body: RefreshIndicator(
        onRefresh: () => ref.refresh(historyProvider.future),
        child: history.when(
          loading: () => const LoadingView(label: 'Loading your creations'),
          error: (error, _) => ErrorView(
            error: error,
            onRetry: () => ref.invalidate(historyProvider),
          ),
          data: (jobs) => jobs.isEmpty
              ? ListView(
                  children: [
                    const SizedBox(height: 120),
                    StatusMessage(
                      icon: Icons.photo_library_outlined,
                      title: 'No creations yet',
                      message:
                          'Wallpapers you generate in the Studio appear here.',
                      actionLabel: 'Open Studio',
                      onAction: () => context.go('/studio'),
                    ),
                  ],
                )
              : GridView.builder(
                  padding: const EdgeInsets.all(Spacing.md),
                  gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                    crossAxisCount: 3,
                    mainAxisSpacing: Spacing.sm,
                    crossAxisSpacing: Spacing.sm,
                    childAspectRatio: 9 / 16,
                  ),
                  itemCount: jobs.length,
                  itemBuilder: (context, index) => _Tile(job: jobs[index]),
                ),
        ),
      ),
    );
  }
}

class _Tile extends StatelessWidget {
  const _Tile({required this.job});

  final Generation job;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final image = job.images.firstOrNull;
    final Widget content;
    if (image != null) {
      content = Image.network(
        image.thumbUrl,
        fit: BoxFit.cover,
        errorBuilder: (context, error, stack) =>
            const Icon(Icons.broken_image_outlined),
      );
    } else {
      content = Center(
        child: Icon(switch (job.status) {
          JobStatus.queued ||
          JobStatus.processing => Icons.hourglass_top_rounded,
          JobStatus.failed => Icons.error_outline_rounded,
          JobStatus.cancelled => Icons.cancel_outlined,
          JobStatus.succeeded => Icons.image_outlined,
        }, color: scheme.onSurfaceVariant),
      );
    }
    return Semantics(
      button: true,
      label: '${job.prompt ?? 'Generation'}, ${job.status.name}',
      child: ClipRRect(
        borderRadius: BorderRadius.circular(Radii.sm),
        child: Material(
          color: scheme.surfaceContainerHigh,
          child: InkWell(
            onTap: () => context.push('/generation/${job.id}'),
            child: content,
          ),
        ),
      ),
    );
  }
}
