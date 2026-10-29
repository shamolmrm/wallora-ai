import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:uuid/uuid.dart';

import '../../../app/providers.dart';
import '../../../core/design_system/tokens.dart';
import '../../../core/design_system/widgets.dart';
import '../../../core/network/api_client.dart';
import '../data/studio_models.dart';

const _aspectLabels = {
  'phone': 'Phone',
  'portrait': 'Portrait',
  'square': 'Square',
  'landscape': 'Landscape',
  'phone_hd': 'Phone HD',
};

class StudioScreen extends ConsumerWidget {
  const StudioScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final capabilities = ref.watch(capabilitiesProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('AI Studio'),
        actions: const [
          _CreditChip(),
          SizedBox(width: Spacing.lg),
        ],
      ),
      body: capabilities.when(
        data: (caps) => _StudioForm(capabilities: caps),
        loading: () => const LoadingView(label: 'Loading studio'),
        error: (error, _) => ErrorView(
          error: error,
          onRetry: () => ref.invalidate(capabilitiesProvider),
        ),
      ),
    );
  }
}

class _CreditChip extends ConsumerWidget {
  const _CreditChip();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final credits = ref.watch(creditsProvider);
    final label = credits.maybeWhen(
      data: (c) => '${c.available}',
      orElse: () => '–',
    );
    return Semantics(
      label: 'Credits available: $label',
      child: Chip(
        avatar: const Icon(Icons.bolt_rounded, size: 18),
        label: Text(label),
      ),
    );
  }
}

class _StudioForm extends ConsumerStatefulWidget {
  const _StudioForm({required this.capabilities});

  final StudioCapabilities capabilities;

  @override
  ConsumerState<_StudioForm> createState() => _StudioFormState();
}

class _StudioFormState extends ConsumerState<_StudioForm> {
  final _prompt = TextEditingController();
  late TierInfo _tier;
  String _aspect = 'portrait';
  String? _preset;
  bool _submitting = false;
  String? _error;
  // Reused while the same submission is retried after a network failure, so a
  // request that actually reached the server is never charged twice.
  String? _pendingKey;

  @override
  void initState() {
    super.initState();
    final tiers = widget.capabilities.tiers
        .where((t) => !t.premiumOnly)
        .toList();
    _tier = tiers.firstWhere(
      (t) => t.name == 'standard',
      orElse: () => tiers.first,
    );
    _aspect = _tier.aspects.contains('phone') ? 'phone' : _tier.aspects.first;
    _prompt.addListener(() => _pendingKey = null);
  }

  @override
  void dispose() {
    _prompt.dispose();
    super.dispose();
  }

  void _selectTier(TierInfo tier) {
    setState(() {
      _tier = tier;
      if (!tier.aspects.contains(_aspect)) _aspect = tier.aspects.first;
      _pendingKey = null;
    });
  }

  Future<void> _generate() async {
    final prompt = _prompt.text.trim();
    if (prompt.length < 3) {
      setState(() => _error = 'Describe your wallpaper in a few words.');
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });
    _pendingKey ??= const Uuid().v4();
    try {
      final job = await ref
          .read(studioRepositoryProvider)
          .create(
            idempotencyKey: _pendingKey!,
            prompt: prompt,
            tier: _tier.name,
            aspect: _aspect,
            stylePreset: _preset,
          );
      _pendingKey = null;
      ref.invalidate(creditsProvider);
      ref.invalidate(historyProvider);
      if (mounted) await context.push('/generation/${job.id}');
    } on ApiException catch (e) {
      if (!e.isNetwork) _pendingKey = null;
      setState(() => _error = messageFor(e));
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final caps = widget.capabilities;
    return ListView(
      padding: const EdgeInsets.all(Spacing.lg),
      children: [
        if (caps.provider == 'fake' &&
            ref.watch(envProvider).showFakeProviderBadge)
          Padding(
            padding: const EdgeInsets.only(bottom: Spacing.md),
            child: Text(
              'Development mode: images come from a free test generator, not AI.',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.tertiary,
              ),
            ),
          ),
        TextField(
          controller: _prompt,
          minLines: 3,
          maxLines: 6,
          maxLength: caps.promptMaxChars,
          textInputAction: TextInputAction.newline,
          decoration: const InputDecoration(
            hintText: 'Misty pine forest at dawn, soft volumetric light…',
            labelText: 'Describe your wallpaper',
            alignLabelWithHint: true,
          ),
        ),
        const SizedBox(height: Spacing.lg),
        Text('Style', style: theme.textTheme.titleSmall),
        const SizedBox(height: Spacing.sm),
        Wrap(
          spacing: Spacing.sm,
          runSpacing: Spacing.sm,
          children: [
            ChoiceChip(
              label: const Text('None'),
              selected: _preset == null,
              onSelected: (_) => setState(() => _preset = null),
            ),
            for (final preset in caps.stylePresets)
              ChoiceChip(
                label: Text(preset[0].toUpperCase() + preset.substring(1)),
                selected: _preset == preset,
                onSelected: (_) => setState(() => _preset = preset),
              ),
          ],
        ),
        const SizedBox(height: Spacing.lg),
        Text('Quality', style: theme.textTheme.titleSmall),
        const SizedBox(height: Spacing.sm),
        Wrap(
          spacing: Spacing.sm,
          runSpacing: Spacing.sm,
          children: [
            for (final tier in caps.tiers)
              ChoiceChip(
                label: Text(
                  '${tier.name[0].toUpperCase()}${tier.name.substring(1)} · '
                  '${tier.credits} cr${tier.premiumOnly ? ' · Premium' : ''}',
                ),
                selected: _tier.name == tier.name,
                onSelected: tier.premiumOnly ? null : (_) => _selectTier(tier),
              ),
          ],
        ),
        const SizedBox(height: Spacing.lg),
        Text('Shape', style: theme.textTheme.titleSmall),
        const SizedBox(height: Spacing.sm),
        Wrap(
          spacing: Spacing.sm,
          runSpacing: Spacing.sm,
          children: [
            for (final aspect in _tier.aspects)
              ChoiceChip(
                label: Text(_aspectLabels[aspect] ?? aspect),
                selected: _aspect == aspect,
                onSelected: (_) => setState(() => _aspect = aspect),
              ),
          ],
        ),
        const SizedBox(height: Spacing.xl),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(bottom: Spacing.md),
            child: Text(
              _error!,
              style: TextStyle(color: theme.colorScheme.error),
            ),
          ),
        FilledButton.icon(
          onPressed: _submitting || !caps.generationEnabled ? null : _generate,
          icon: _submitting
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.auto_awesome_rounded),
          label: Text(
            caps.generationEnabled
                ? 'Generate · ${_tier.credits} credits'
                : 'Generation is paused',
          ),
        ),
      ],
    );
  }
}
