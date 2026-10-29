import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../core/design_system/theme.dart';
import 'router.dart';

class WalloraApp extends StatefulWidget {
  const WalloraApp({super.key, this.router});

  /// Injectable for tests.
  final GoRouter? router;

  @override
  State<WalloraApp> createState() => _WalloraAppState();
}

class _WalloraAppState extends State<WalloraApp> {
  late final GoRouter _router = widget.router ?? buildRouter();

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'Wallora AI',
      debugShowCheckedModeBanner: false,
      themeMode: ThemeMode.system,
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      routerConfig: _router,
    );
  }
}
