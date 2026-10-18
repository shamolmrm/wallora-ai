import 'package:flutter/material.dart';

import 'app/app.dart';
import 'app/env.dart';

void main() {
  runApp(WalloraApp(env: AppEnv.fromEnvironment()));
}
