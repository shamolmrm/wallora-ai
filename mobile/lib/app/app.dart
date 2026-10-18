import 'package:flutter/material.dart';

import 'env.dart';

/// Root widget. Phase 3 replaces the placeholder home with the design system,
/// router and feature screens.
class WalloraApp extends StatelessWidget {
  const WalloraApp({super.key, required this.env});

  final AppEnv env;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Wallora AI',
      debugShowCheckedModeBanner: false,
      themeMode: ThemeMode.system,
      theme: ThemeData(colorSchemeSeed: const Color(0xFF5B5BD6)),
      darkTheme: ThemeData(
        colorSchemeSeed: const Color(0xFF5B5BD6),
        brightness: Brightness.dark,
      ),
      home: _PlaceholderHome(env: env),
    );
  }
}

class _PlaceholderHome extends StatelessWidget {
  const _PlaceholderHome({required this.env});

  final AppEnv env;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text('Wallora AI', style: textTheme.headlineMedium),
                const SizedBox(height: 8),
                Text(
                  'Foundation build (${env.flavor}). Features are not implemented yet.',
                  style: textTheme.bodyMedium,
                  textAlign: TextAlign.center,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
