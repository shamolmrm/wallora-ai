import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/design_system/tokens.dart';
import '../../core/design_system/widgets.dart';
import '../studio/data/studio_models.dart';

const _entryLabels = {
  'grant': 'Credits added',
  'reserve': 'Generation started',
  'capture': 'Generation completed',
  'release': 'Refunded',
  'expire': 'Expired',
  'revoke': 'Removed',
  'admin_adjust': 'Adjustment',
};

class ProfileScreen extends ConsumerWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final credits = ref.watch(creditsProvider);
    final ledger = ref.watch(ledgerProvider);
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Profile')),
      body: RefreshIndicator(
        onRefresh: () async {
          ref
            ..invalidate(creditsProvider)
            ..invalidate(ledgerProvider);
        },
        child: ListView(
          padding: const EdgeInsets.all(Spacing.lg),
          children: [
            Card(
              child: Padding(
                padding: const EdgeInsets.all(Spacing.xl),
                child: credits.when(
                  loading: () => const LoadingView(),
                  error: (error, _) => ErrorView(
                    error: error,
                    onRetry: () => ref.invalidate(creditsProvider),
                  ),
                  data: (c) => _Balance(balance: c),
                ),
              ),
            ),
            const SizedBox(height: Spacing.xl),
            Text('Credit history', style: theme.textTheme.titleMedium),
            const SizedBox(height: Spacing.sm),
            ...ledger.when(
              loading: () => [const LoadingView()],
              error: (error, _) => [
                ErrorView(
                  error: error,
                  onRetry: () => ref.invalidate(ledgerProvider),
                ),
              ],
              data: (entries) => entries.isEmpty
                  ? [const Text('No credit activity yet.')]
                  : [for (final entry in entries) _LedgerRow(entry: entry)],
            ),
          ],
        ),
      ),
    );
  }
}

class _Balance extends StatelessWidget {
  const _Balance({required this.balance});

  final CreditBalance balance;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Available credits', style: theme.textTheme.labelLarge),
        Text(
          '${balance.available}',
          style: theme.textTheme.displaySmall?.copyWith(
            fontWeight: FontWeight.w700,
          ),
        ),
        if (balance.held > 0)
          Text('${balance.held} reserved for generations in progress'),
        const SizedBox(height: Spacing.sm),
        Text(
          'Free daily credit resets at '
          '${TimeOfDay.fromDateTime(balance.dailyFreeResetsAt.toLocal()).format(context)}',
          style: theme.textTheme.bodySmall,
        ),
      ],
    );
  }
}

class _LedgerRow extends StatelessWidget {
  const _LedgerRow({required this.entry});

  final LedgerEntry entry;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final sign = entry.amount > 0 ? '+' : '';
    return ListTile(
      contentPadding: EdgeInsets.zero,
      title: Text(_entryLabels[entry.entryType] ?? entry.entryType),
      subtitle: Text(
        MaterialLocalizations.of(
          context,
        ).formatShortDate(entry.createdAt.toLocal()),
      ),
      trailing: Text(
        entry.amount == 0 ? '—' : '$sign${entry.amount}',
        style: TextStyle(
          fontWeight: FontWeight.w600,
          color: entry.amount < 0 ? scheme.error : scheme.primary,
        ),
      ),
    );
  }
}
