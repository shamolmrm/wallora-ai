import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:uuid/uuid.dart';

import '../../../app/providers.dart';
import '../../../core/design_system/tokens.dart';
import '../../../core/design_system/widgets.dart';
import '../../../core/network/api_client.dart';
import '../data/studio_models.dart';

const _failureMessages = {
  'content_blocked':
      "This result didn't pass our safety checks. Your credits were refunded.",
  'provider_rejected': 'The generator rejected this request. Credits refunded.',
  'lease_expired': 'Generation took too long. Your credits were refunded.',
};

/// Shows one generation: polls while it is queued/processing, then the result.
class GenerationScreen extends ConsumerStatefulWidget {
  const GenerationScreen({super.key, required this.id});

  final String id;

  @override
  ConsumerState<GenerationScreen> createState() => _GenerationScreenState();
}

class _GenerationScreenState extends ConsumerState<GenerationScreen> {
  Generation? _job;
  Object? _error;
  Timer? _timer;
  int _pollCount = 0;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final job = await ref.read(studioRepositoryProvider).get(widget.id);
      if (!mounted) return;
      setState(() {
        _job = job;
        _error = null;
      });
      if (job.isTerminal) {
        ref.invalidate(creditsProvider);
        ref.invalidate(historyProvider);
      } else {
        // Back off from 1 s to 5 s while waiting.
        final delay = Duration(seconds: (1 + _pollCount ~/ 3).clamp(1, 5));
        _pollCount++;
        _timer = Timer(delay, () => unawaited(_load()));
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  Future<void> _cancel() async {
    setState(() => _busy = true);
    try {
      final job = await ref.read(studioRepositoryProvider).cancel(widget.id);
      _timer?.cancel();
      setState(() => _job = job);
      ref.invalidate(creditsProvider);
    } on ApiException catch (e) {
      _showSnack(
        e.code == 'not_cancellable'
            ? "It's already being generated and can't be cancelled."
            : messageFor(e),
      );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _retry() async {
    setState(() => _busy = true);
    try {
      final job = await ref
          .read(studioRepositoryProvider)
          .retry(widget.id, idempotencyKey: const Uuid().v4());
      ref.invalidate(creditsProvider);
      if (mounted) context.pushReplacement('/generation/${job.id}');
    } on ApiException catch (e) {
      _showSnack(messageFor(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _showSnack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final job = _job;
    return Scaffold(
      appBar: AppBar(title: const Text('Your wallpaper')),
      body: switch ((job, _error)) {
        (null, final Object error) => ErrorView(error: error, onRetry: _load),
        (null, _) => const LoadingView(label: 'Loading generation'),
        (final Generation job, _) => _body(context, job),
      },
    );
  }

  Widget _body(BuildContext context, Generation job) {
    final theme = Theme.of(context);
    switch (job.status) {
      case JobStatus.queued:
      case JobStatus.processing:
        return Padding(
          padding: const EdgeInsets.all(Spacing.xl),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const LoadingView(label: 'Generating'),
              const SizedBox(height: Spacing.xl),
              Text(
                job.status == JobStatus.queued
                    ? 'Waiting in line…'
                    : 'Creating your wallpaper…',
                style: theme.textTheme.titleMedium,
              ),
              if (job.attempts > 1) ...[
                const SizedBox(height: Spacing.sm),
                Text('Retrying (attempt ${job.attempts})'),
              ],
              const SizedBox(height: Spacing.xl),
              if (job.status == JobStatus.queued)
                OutlinedButton(
                  onPressed: _busy ? null : _cancel,
                  child: const Text('Cancel'),
                )
              else
                Text(
                  'This can no longer be cancelled.',
                  style: theme.textTheme.bodySmall,
                ),
            ],
          ),
        );
      case JobStatus.succeeded:
        final image = job.images.firstOrNull;
        if (image == null) {
          return StatusMessage(
            icon: Icons.image_not_supported_outlined,
            title: 'Image unavailable',
            actionLabel: 'Reload',
            onAction: _load,
          );
        }
        return _ResultView(job: job, image: image);
      case JobStatus.failed:
        return StatusMessage(
          icon: Icons.sentiment_dissatisfied_rounded,
          title: "This one didn't work out",
          message:
              _failureMessages[job.errorCode] ??
              'Something went wrong. Your credits were refunded.',
          actionLabel: _busy ? null : 'Try again',
          onAction: _retry,
        );
      case JobStatus.cancelled:
        return StatusMessage(
          icon: Icons.cancel_outlined,
          title: 'Cancelled',
          message: 'Your credits were refunded.',
          actionLabel: _busy ? null : 'Generate again',
          onAction: _retry,
        );
    }
  }
}

class _ResultView extends StatelessWidget {
  const _ResultView({required this.job, required this.image});

  final Generation job;
  final GeneratedImage image;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListView(
      padding: const EdgeInsets.all(Spacing.lg),
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(Radii.lg),
          child: AspectRatio(
            aspectRatio: image.width / image.height,
            child: Image.network(
              image.url,
              fit: BoxFit.cover,
              semanticLabel: job.prompt ?? 'Generated wallpaper',
              loadingBuilder: (context, child, progress) =>
                  progress == null ? child : const LoadingView(),
              errorBuilder: (context, error, stack) => const StatusMessage(
                icon: Icons.broken_image_outlined,
                title: "Couldn't load the image",
                message: 'The link may have expired. Go back and reopen it.',
              ),
            ),
          ),
        ),
        const SizedBox(height: Spacing.lg),
        if (job.prompt != null)
          Text(job.prompt!, style: theme.textTheme.bodyLarge),
        const SizedBox(height: Spacing.sm),
        Text(
          '${image.width} × ${image.height} · ${job.tier} · ${job.creditCost} credits',
          style: theme.textTheme.bodySmall?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
        const SizedBox(height: Spacing.lg),
        Text(
          'Saving to your gallery and setting as wallpaper arrive in the next mobile update.',
          style: theme.textTheme.bodySmall?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
      ],
    );
  }
}
