/// Build-time configuration injected with `--dart-define-from-file`.
///
/// These values are public identifiers, not secrets: anything compiled into
/// the app binary can be extracted. Private keys stay on the backend.
class AppEnv {
  const AppEnv({
    required this.flavor,
    required this.apiBaseUrl,
    required this.showFakeProviderBadge,
    this.devAuthToken = '',
  });

  factory AppEnv.fromEnvironment() {
    return const AppEnv(
      flavor: String.fromEnvironment('FLAVOR', defaultValue: 'dev'),
      apiBaseUrl: String.fromEnvironment(
        'API_BASE_URL',
        defaultValue: 'http://127.0.0.1:8000',
      ),
      showFakeProviderBadge: bool.fromEnvironment(
        'ENABLE_FAKE_PROVIDER_BADGE',
        defaultValue: true,
      ),
      // Local development only, until Firebase Auth is configured. The backend
      // accepts these tokens only when DEV_AUTH_ENABLED is set (local/test).
      devAuthToken: String.fromEnvironment('DEV_AUTH_TOKEN'),
    );
  }

  final String flavor;
  final String apiBaseUrl;
  final bool showFakeProviderBadge;
  final String devAuthToken;

  bool get isProduction => flavor == 'prod';
}
