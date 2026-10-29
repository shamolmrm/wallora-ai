import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../features/discover/discover_screen.dart';
import '../features/library/library_screen.dart';
import '../features/profile/profile_screen.dart';
import '../features/studio/presentation/generation_screen.dart';
import '../features/studio/presentation/studio_screen.dart';

GoRouter buildRouter({String initialLocation = '/studio'}) => GoRouter(
  initialLocation: initialLocation,
  routes: [
    StatefulShellRoute.indexedStack(
      builder: (context, state, shell) => _Shell(shell: shell),
      branches: [
        _branch('/discover', const DiscoverScreen()),
        _branch('/search', const SearchScreen()),
        _branch('/studio', const StudioScreen()),
        _branch('/library', const LibraryScreen()),
        _branch('/profile', const ProfileScreen()),
      ],
    ),
    GoRoute(
      path: '/category/:slug',
      builder: (context, state) => CategoryScreen(
        slug: state.pathParameters['slug']!,
        name:
            state.uri.queryParameters['name'] ?? state.pathParameters['slug']!,
      ),
    ),
    GoRoute(
      path: '/wallpaper/:id',
      builder: (context, state) =>
          WallpaperScreen(id: state.pathParameters['id']!),
    ),
    GoRoute(
      path: '/generation/:id',
      builder: (context, state) =>
          GenerationScreen(id: state.pathParameters['id']!),
    ),
  ],
);

StatefulShellBranch _branch(String path, Widget screen) => StatefulShellBranch(
  routes: [GoRoute(path: path, builder: (context, state) => screen)],
);

class _Shell extends StatelessWidget {
  const _Shell({required this.shell});

  final StatefulNavigationShell shell;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: shell,
      bottomNavigationBar: NavigationBar(
        selectedIndex: shell.currentIndex,
        onDestinationSelected: (index) =>
            shell.goBranch(index, initialLocation: index == shell.currentIndex),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.explore_outlined),
            selectedIcon: Icon(Icons.explore),
            label: 'Discover',
          ),
          NavigationDestination(
            icon: Icon(Icons.search_rounded),
            label: 'Search',
          ),
          NavigationDestination(
            icon: Icon(Icons.auto_awesome_outlined),
            selectedIcon: Icon(Icons.auto_awesome),
            label: 'Studio',
          ),
          NavigationDestination(
            icon: Icon(Icons.photo_library_outlined),
            selectedIcon: Icon(Icons.photo_library),
            label: 'Library',
          ),
          NavigationDestination(
            icon: Icon(Icons.person_outline_rounded),
            selectedIcon: Icon(Icons.person_rounded),
            label: 'Profile',
          ),
        ],
      ),
    );
  }
}
