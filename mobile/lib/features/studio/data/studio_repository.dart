import '../../../core/network/api_client.dart';
import 'studio_models.dart';

class StudioRepository {
  StudioRepository(this._api);

  final ApiClient _api;

  Future<StudioCapabilities> capabilities() async =>
      StudioCapabilities.fromJson(
        await _api.getJson('/v1/studio/capabilities'),
      );

  Future<Generation> create({
    required String idempotencyKey,
    required String prompt,
    required String tier,
    required String aspect,
    String? stylePreset,
  }) async {
    final json = await _api.postJson(
      '/v1/generations',
      idempotencyKey: idempotencyKey,
      body: {
        'kind': 'generate',
        'prompt': prompt,
        'tier': tier,
        'aspect': aspect,
        'style_preset': ?stylePreset,
      },
    );
    return Generation.fromJson(json);
  }

  Future<Generation> get(String id) async =>
      Generation.fromJson(await _api.getJson('/v1/generations/$id'));

  Future<Generation> cancel(String id) async =>
      Generation.fromJson(await _api.postJson('/v1/generations/$id/cancel'));

  Future<Generation> retry(String id, {required String idempotencyKey}) async =>
      Generation.fromJson(
        await _api.postJson(
          '/v1/generations/$id/retry',
          idempotencyKey: idempotencyKey,
        ),
      );

  Future<List<Generation>> history({int limit = 30}) async => parseItems(
    await _api.getJson('/v1/generations', query: {'limit': limit}),
    Generation.fromJson,
  );

  Future<CreditBalance> credits() async =>
      CreditBalance.fromJson(await _api.getJson('/v1/me/credits'));

  Future<List<LedgerEntry>> ledger({int limit = 50}) async => parseItems(
    await _api.getJson('/v1/me/credits/ledger', query: {'limit': limit}),
    LedgerEntry.fromJson,
  );
}
