import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:wallora/app/app.dart';
import 'package:wallora/app/env.dart';
import 'package:wallora/app/providers.dart';
import 'package:wallora/app/router.dart';
import 'package:wallora/core/network/api_client.dart';
import 'package:wallora/features/discover/data/catalog.dart';
import 'package:wallora/features/studio/data/studio_models.dart';

const _env = AppEnv(
  flavor: 'test',
  apiBaseUrl: 'http://localhost:8000',
  showFakeProviderBadge: true,
);

final _capabilities = StudioCapabilities.fromJson({
  'provider': 'fake',
  'generation_enabled': true,
  'tiers': [
    {
      'name': 'draft',
      'credits': 1,
      'premium_only': false,
      'aspects': ['portrait', 'square'],
    },
    {
      'name': 'standard',
      'credits': 3,
      'premium_only': false,
      'aspects': ['phone', 'portrait'],
    },
    {
      'name': 'hd',
      'credits': 8,
      'premium_only': true,
      'aspects': ['phone'],
    },
  ],
  'style_presets': ['minimal', 'anime'],
  'prompt_max_chars': 1000,
});

var _discover = const DiscoverData(featured: [], categories: [], latest: []);

Widget _app(String location) => ProviderScope(
  overrides: [
    envProvider.overrideWithValue(_env),
    capabilitiesProvider.overrideWith((ref) async => _capabilities),
    creditsProvider.overrideWith(
      (ref) async => CreditBalance(
        available: 7,
        held: 0,
        dailyFreeResetsAt: DateTime.utc(2026, 10, 19),
      ),
    ),
    historyProvider.overrideWith((ref) async => <Generation>[]),
    discoverProvider.overrideWith((ref) async => _discover),
  ],
  child: WalloraApp(router: buildRouter(initialLocation: location)),
);

/// Phone-sized surface (1080x2400 @3x) so the whole Studio form is laid out.
void _usePhoneSize(WidgetTester tester) {
  tester.view.physicalSize = const Size(1080, 2400);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
}

Future<void> _scrollTo(WidgetTester tester, Finder finder) => tester
    .scrollUntilVisible(finder, 200, scrollable: find.byType(Scrollable).first);

void main() {
  testWidgets('studio shows tiers, credit cost and balance', (tester) async {
    _usePhoneSize(tester);
    await tester.pumpWidget(_app('/studio'));
    await tester.pumpAndSettle();

    expect(find.text('AI Studio'), findsOneWidget);
    expect(find.text('7'), findsOneWidget);
    expect(find.textContaining('Hd · 8 cr · Premium'), findsOneWidget);
    await _scrollTo(tester, find.text('Generate · 3 credits'));
    expect(find.text('Generate · 3 credits'), findsOneWidget);
    expect(find.textContaining('Development mode'), findsOneWidget);
  });

  testWidgets('choosing a tier updates the cost and valid shapes', (
    tester,
  ) async {
    _usePhoneSize(tester);
    await tester.pumpWidget(_app('/studio'));
    await tester.pumpAndSettle();

    await tester.tap(find.textContaining('Draft · 1 cr'));
    await tester.pumpAndSettle();

    await _scrollTo(tester, find.text('Generate · 1 credits'));
    expect(find.text('Generate · 1 credits'), findsOneWidget);
    expect(
      find.text('Phone'),
      findsNothing,
    ); // draft model only supports fixed sizes
  });

  testWidgets('a too-short prompt is rejected locally', (tester) async {
    _usePhoneSize(tester);
    await tester.pumpWidget(_app('/studio'));
    await tester.pumpAndSettle();

    await _scrollTo(tester, find.text('Generate · 3 credits'));
    await tester.tap(find.text('Generate · 3 credits'));
    await tester.pump();

    expect(
      find.text('Describe your wallpaper in a few words.'),
      findsOneWidget,
    );
  });

  testWidgets('discover shows an empty state when the catalog is empty', (
    tester,
  ) async {
    _discover = const DiscoverData(featured: [], categories: [], latest: []);
    await tester.pumpWidget(_app('/discover'));
    await tester.pumpAndSettle();

    expect(find.text('No wallpapers yet'), findsOneWidget);
    expect(find.byType(NavigationBar), findsOneWidget);
  });

  testWidgets('discover lists categories and latest wallpapers', (
    tester,
  ) async {
    _usePhoneSize(tester);
    const w = Wallpaper(
      id: 'w1',
      title: 'Misty forest',
      category: 'nature',
      width: 1072,
      height: 2320,
      thumbUrl: 'http://x/t',
      url: 'http://x/f',
      isPremium: false,
      isAiGenerated: true,
    );
    _discover = const DiscoverData(
      featured: [w],
      categories: [Category(slug: 'nature', name: 'Nature', count: 1)],
      latest: [w],
    );
    await tester.pumpWidget(_app('/discover'));
    await tester.pump();

    expect(find.text('Nature'), findsOneWidget);
    expect(find.text('Latest'), findsOneWidget);
    expect(find.bySemanticsLabel('Misty forest'), findsNWidgets(2));
  });

  testWidgets('library empty state links to the studio', (tester) async {
    await tester.pumpWidget(_app('/library'));
    await tester.pumpAndSettle();

    expect(find.text('No creations yet'), findsOneWidget);
    await tester.tap(find.text('Open Studio'));
    await tester.pumpAndSettle();
    expect(find.text('AI Studio'), findsOneWidget);
  });

  test('generation JSON parses', () {
    final job = Generation.fromJson({
      'id': 'abc',
      'status': 'succeeded',
      'prompt': 'forest',
      'tier': 'standard',
      'aspect': 'phone',
      'credit_cost': 3,
      'attempts': 1,
      'error_code': null,
      'created_at': '2026-10-18T12:00:00Z',
      'images': [
        {
          'id': 'img',
          'width': 1072,
          'height': 2320,
          'url': 'http://x/full',
          'thumb_url': 'http://x/thumb',
          'mime_type': 'image/webp',
          'url_expires_in': 600,
        },
      ],
    });
    expect(job.isTerminal, isTrue);
    expect(job.images.single.width, 1072);
  });

  test('error messages map backend codes', () {
    expect(
      messageFor(
        const ApiException(code: 'insufficient_credits', message: 'x'),
      ),
      "You don't have enough credits for this.",
    );
    expect(
      const ApiException(code: 'network_error', message: 'x').isNetwork,
      isTrue,
    );
  });
}
