import 'package:flutter/material.dart';

/// Design tokens. Widgets use these instead of ad-hoc values.
abstract final class Spacing {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;
}

abstract final class Radii {
  static const double sm = 8;
  static const double md = 16;
  static const double lg = 24;
}

abstract final class Motion {
  static const Duration fast = Duration(milliseconds: 150);
  static const Duration normal = Duration(milliseconds: 250);
}

abstract final class Palette {
  static const Color accent = Color(0xFF7C6CF6);
  static const Color inkDark = Color(0xFF0E0F13);
  static const Color surfaceDark = Color(0xFF17181D);
  static const Color surfaceDarkHigh = Color(0xFF22232A);
  static const Color paperLight = Color(0xFFF7F7F9);
  static const Color surfaceLightHigh = Color(0xFFECECF1);
}
