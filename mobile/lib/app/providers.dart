import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/network/api_client.dart';
import '../features/studio/data/studio_models.dart';
import '../features/studio/data/studio_repository.dart';
import 'env.dart';

/// Overridden in `main.dart` with the build-time environment.
final envProvider = Provider<AppEnv>((ref) => throw UnimplementedError());

final apiClientProvider = Provider<ApiClient>((ref) {
  final env = ref.watch(envProvider);
  return ApiClient(baseUrl: env.apiBaseUrl, authToken: env.devAuthToken);
});

final studioRepositoryProvider = Provider<StudioRepository>(
  (ref) => StudioRepository(ref.watch(apiClientProvider)),
);

final capabilitiesProvider = FutureProvider<StudioCapabilities>(
  (ref) => ref.watch(studioRepositoryProvider).capabilities(),
);

final creditsProvider = FutureProvider<CreditBalance>(
  (ref) => ref.watch(studioRepositoryProvider).credits(),
);

final historyProvider = FutureProvider<List<Generation>>(
  (ref) => ref.watch(studioRepositoryProvider).history(),
);

final ledgerProvider = FutureProvider<List<LedgerEntry>>(
  (ref) => ref.watch(studioRepositoryProvider).ledger(),
);
