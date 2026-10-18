import 'package:flutter_test/flutter_test.dart';
import 'package:wallora/app/app.dart';
import 'package:wallora/app/env.dart';

void main() {
  const env = AppEnv(
    flavor: 'test',
    apiBaseUrl: 'http://localhost:8000',
    showFakeProviderBadge: true,
  );

  testWidgets('renders the app title and flavor', (tester) async {
    await tester.pumpWidget(const WalloraApp(env: env));

    expect(find.text('Wallora AI'), findsOneWidget);
    expect(find.textContaining('(test)'), findsOneWidget);
  });

  test('isProduction is true only for the prod flavor', () {
    expect(env.isProduction, isFalse);
    expect(
      const AppEnv(
        flavor: 'prod',
        apiBaseUrl: 'https://api.example.com',
        showFakeProviderBadge: false,
      ).isProduction,
      isTrue,
    );
  });
}
