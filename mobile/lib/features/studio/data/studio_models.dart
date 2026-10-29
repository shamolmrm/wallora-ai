/// Models for the backend Studio API (`/v1/studio/*`, `/v1/generations*`).
library;

List<Map<String, Object?>> _maps(Object? value) =>
    (value as List<Object?>? ?? const []).cast<Map<String, Object?>>();

class TierInfo {
  const TierInfo({
    required this.name,
    required this.credits,
    required this.premiumOnly,
    required this.aspects,
  });

  factory TierInfo.fromJson(Map<String, Object?> json) => TierInfo(
    name: json['name']! as String,
    credits: json['credits']! as int,
    premiumOnly: json['premium_only']! as bool,
    aspects: (json['aspects']! as List<Object?>).cast<String>(),
  );

  final String name;
  final int credits;
  final bool premiumOnly;
  final List<String> aspects;
}

class StudioCapabilities {
  const StudioCapabilities({
    required this.provider,
    required this.generationEnabled,
    required this.tiers,
    required this.stylePresets,
    required this.promptMaxChars,
  });

  factory StudioCapabilities.fromJson(Map<String, Object?> json) =>
      StudioCapabilities(
        provider: json['provider']! as String,
        generationEnabled: json['generation_enabled']! as bool,
        tiers: _maps(json['tiers']).map(TierInfo.fromJson).toList(),
        stylePresets: (json['style_presets']! as List<Object?>).cast<String>(),
        promptMaxChars: json['prompt_max_chars']! as int,
      );

  final String provider;
  final bool generationEnabled;
  final List<TierInfo> tiers;
  final List<String> stylePresets;
  final int promptMaxChars;
}

class GeneratedImage {
  const GeneratedImage({
    required this.id,
    required this.width,
    required this.height,
    required this.url,
    required this.thumbUrl,
  });

  factory GeneratedImage.fromJson(Map<String, Object?> json) => GeneratedImage(
    id: json['id']! as String,
    width: json['width']! as int,
    height: json['height']! as int,
    url: json['url']! as String,
    thumbUrl: json['thumb_url']! as String,
  );

  final String id;
  final int width;
  final int height;
  final String url;
  final String thumbUrl;
}

enum JobStatus { queued, processing, succeeded, failed, cancelled }

class Generation {
  const Generation({
    required this.id,
    required this.status,
    required this.prompt,
    required this.tier,
    required this.aspect,
    required this.creditCost,
    required this.attempts,
    required this.errorCode,
    required this.createdAt,
    required this.images,
  });

  factory Generation.fromJson(Map<String, Object?> json) => Generation(
    id: json['id']! as String,
    status: JobStatus.values.byName(json['status']! as String),
    prompt: json['prompt'] as String?,
    tier: json['tier']! as String,
    aspect: json['aspect']! as String,
    creditCost: json['credit_cost']! as int,
    attempts: json['attempts']! as int,
    errorCode: json['error_code'] as String?,
    createdAt: DateTime.parse(json['created_at']! as String),
    images: _maps(json['images']).map(GeneratedImage.fromJson).toList(),
  );

  final String id;
  final JobStatus status;
  final String? prompt;
  final String tier;
  final String aspect;
  final int creditCost;
  final int attempts;
  final String? errorCode;
  final DateTime createdAt;
  final List<GeneratedImage> images;

  bool get isTerminal =>
      status == JobStatus.succeeded ||
      status == JobStatus.failed ||
      status == JobStatus.cancelled;
}

class CreditBalance {
  const CreditBalance({
    required this.available,
    required this.held,
    required this.dailyFreeResetsAt,
  });

  factory CreditBalance.fromJson(Map<String, Object?> json) => CreditBalance(
    available: json['available']! as int,
    held: json['held']! as int,
    dailyFreeResetsAt: DateTime.parse(json['daily_free_resets_at']! as String),
  );

  final int available;
  final int held;
  final DateTime dailyFreeResetsAt;
}

class LedgerEntry {
  const LedgerEntry({
    required this.entryType,
    required this.amount,
    required this.availableAfter,
    required this.reason,
    required this.createdAt,
  });

  factory LedgerEntry.fromJson(Map<String, Object?> json) => LedgerEntry(
    entryType: json['entry_type']! as String,
    amount: json['amount']! as int,
    availableAfter: json['available_after']! as int,
    reason: json['reason'] as String?,
    createdAt: DateTime.parse(json['created_at']! as String),
  );

  final String entryType;
  final int amount;
  final int availableAfter;
  final String? reason;
  final DateTime createdAt;
}

List<T> parseItems<T>(
  Map<String, Object?> page,
  T Function(Map<String, Object?>) fromJson,
) => _maps(page['items']).map(fromJson).toList();
