import 'package:dio/dio.dart';

/// A failed API call. [code] is the stable problem code from the backend
/// (RFC 9457 `code`), or `network_error` when the server was unreachable.
class ApiException implements Exception {
  const ApiException({
    required this.code,
    required this.message,
    this.status,
    this.extra = const {},
  });

  final String code;
  final String message;
  final int? status;
  final Map<String, Object?> extra;

  bool get isNetwork => code == 'network_error';

  @override
  String toString() => 'ApiException($status, $code)';
}

/// User-facing message for an error. Uses the server's safe `title` when present.
String messageFor(Object error) {
  if (error is ApiException) {
    return switch (error.code) {
      'network_error' => "Can't reach Wallora. Check your connection.",
      'insufficient_credits' => "You don't have enough credits for this.",
      'prompt_rejected' =>
        "This prompt can't be used. Please try a different idea.",
      'rate_limited' => 'Too many requests. Please wait a moment.',
      'daily_limit_reached' => "You've reached today's generation limit.",
      'generation_paused' => 'Generation is paused right now. Try later.',
      'moderation_unavailable' => 'Safety checks are unavailable. Try again.',
      'busy' => 'Wallora is busy. Please try again.',
      _ => error.message,
    };
  }
  return 'Something went wrong.';
}

class ApiClient {
  ApiClient({required String baseUrl, required String authToken})
    : _dio = Dio(
        BaseOptions(
          baseUrl: baseUrl,
          connectTimeout: const Duration(seconds: 10),
          receiveTimeout: const Duration(seconds: 30),
          headers: {'Authorization': 'Bearer $authToken'},
        ),
      );

  final Dio _dio;

  Future<Map<String, Object?>> getJson(
    String path, {
    Map<String, Object?>? query,
  }) => _send(() => _dio.get<Object?>(path, queryParameters: query));

  Future<Map<String, Object?>> postJson(
    String path, {
    Object? body,
    String? idempotencyKey,
  }) => _send(
    () => _dio.post<Object?>(
      path,
      data: body,
      options: Options(headers: {'Idempotency-Key': ?idempotencyKey}),
    ),
  );

  Future<Map<String, Object?>> _send(
    Future<Response<Object?>> Function() call,
  ) async {
    try {
      final response = await call();
      final data = response.data;
      return data is Map<String, Object?> ? data : <String, Object?>{};
    } on DioException catch (e) {
      throw _toApiException(e);
    }
  }

  static ApiException _toApiException(DioException e) {
    final response = e.response;
    if (response == null) {
      return const ApiException(
        code: 'network_error',
        message: "Can't reach the server.",
      );
    }
    final data = response.data;
    if (data is Map<String, Object?>) {
      return ApiException(
        code: (data['code'] as String?) ?? 'http_error',
        message: (data['title'] as String?) ?? 'Request failed.',
        status: response.statusCode,
        extra: data,
      );
    }
    return ApiException(
      code: 'http_error',
      message: 'Request failed.',
      status: response.statusCode,
    );
  }
}
