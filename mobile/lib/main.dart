import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app/app.dart';
import 'app/env.dart';
import 'app/providers.dart';

void main() {
  runApp(
    ProviderScope(
      overrides: [envProvider.overrideWithValue(AppEnv.fromEnvironment())],
      child: const WalloraApp(),
    ),
  );
}
