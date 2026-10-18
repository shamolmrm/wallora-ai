/// Build-time configuration injected with `--dart-define-from-file`.
///
/// These values are public identifiers, not secrets: anything compiled into
/// the app binary can be extracted. Private keys stay on the backend.
class AppEnv {
  const AppEnv({
    required this.flavor,
    required this.apiBaseUrl,
    required this.showFakeProviderBadge,
  });

  factory AppEnv.fromEnvironment() {
    return const AppEnv(
      flavor: String.fromEnvironment('FLAVOR', defaultValue: 'dev'),
      apiBaseUrl: String.fromEnvironment(
        'API_BASE_URL',
        defaultValue: 'http://10.0.2.2:8000',
      ),
      showFakeProviderBadge: bool.fromEnvironment(
        'ENABLE_FAKE_PROVIDER_BADGE',
        defaultValue: true,
      ),
    );
  }

  final String flavor;
  final String apiBaseUrl;
  final bool showFakeProviderBadge;

  bool get isProduction => flavor == 'prod';
}
