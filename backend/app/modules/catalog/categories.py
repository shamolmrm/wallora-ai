"""Launch categories and the in-house AI prompts used to create their wallpapers.

Prompts describe original scenes only: no artists' names, brands, logos,
characters or real people, so generated catalog images stay free of
third-party rights.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CategorySeed:
    slug: str
    name: str
    prompts: tuple[str, ...]


CATEGORY_SEEDS: tuple[CategorySeed, ...] = (
    CategorySeed(
        "nature",
        "Nature",
        (
            "Misty pine forest at dawn with soft volumetric light through the trees",
            "Turquoise mountain lake surrounded by snowy peaks under a clear sky",
            "Field of wildflowers at golden hour with gentle bokeh",
            "Moss-covered waterfall in a quiet rainforest gorge",
        ),
    ),
    CategorySeed(
        "space",
        "Space",
        (
            "Swirling violet and teal nebula with scattered bright stars",
            "Ringed gas giant rising over a rocky alien horizon",
            "Milky Way arching over a dark desert under a starry sky",
            "Distant spiral galaxy glowing in deep black space",
        ),
    ),
    CategorySeed(
        "abstract",
        "Abstract",
        (
            "Flowing liquid gradients of coral, peach and lavender",
            "Layered paper-cut waves in deep blue tones with soft shadows",
            "Geometric glass shapes refracting rainbow light on a dark background",
            "Smooth marble swirls of emerald and gold",
        ),
    ),
    CategorySeed(
        "minimal",
        "Minimal",
        (
            "Single crescent moon over a calm pastel sky, generous negative space",
            "Lone tree on a gentle hill under a pale sky, minimalist",
            "Soft sand dunes with clean curved shadows in warm beige",
            "Simple sunrise gradient with one thin horizon line",
        ),
    ),
    CategorySeed(
        "amoled",
        "AMOLED",
        (
            "Pure black background with a thin glowing neon blue line wave",
            "Single glowing jellyfish drifting in pure black darkness",
            "Pure black background with a small luminous crescent moon",
            "Faint golden particles drifting on pure black",
        ),
    ),
    CategorySeed(
        "fantasy",
        "Fantasy",
        (
            "Floating island castle above the clouds at sunset, painterly",
            "Glowing enchanted forest path with luminous mushrooms",
            "Ancient stone dragon statue overgrown with ivy in morning mist",
            "Crystal cavern lit by soft blue light",
        ),
    ),
    CategorySeed(
        "anime",
        "Anime",
        (
            "Anime-style city street at dusk with glowing shop signs without text, no people",
            "Anime-style countryside train station surrounded by summer clouds, no people",
            "Anime-style cherry blossom path at night under lanterns, no people",
            "Anime-style rooftop view of a sunset skyline, no people",
        ),
    ),
    CategorySeed(
        "cars",
        "Cars",
        (
            "Sleek unbranded concept sports car on a wet neon-lit road at night",
            "Unbranded classic-style coupe on a coastal road at sunset",
            "Close-up of an unbranded car headlight with dramatic reflections",
            "Unbranded off-road vehicle on a desert trail under a starry sky",
        ),
    ),
    CategorySeed(
        "architecture",
        "Architecture",
        (
            "Minimal white curved building facade against a blue sky",
            "Spiral staircase seen from above with warm light",
            "Futuristic glass skyscrapers reflecting a pink sunset",
            "Quiet stone courtyard with arches and soft afternoon light",
        ),
    ),
    CategorySeed(
        "animals",
        "Animals",
        (
            "Fox resting in snowy woods with soft falling snow",
            "Hummingbird hovering near a tropical flower, shallow depth of field",
            "Wolf silhouette on a ridge under a full moon",
            "Koi fish swimming in a clear pond with lily pads",
        ),
    ),
    CategorySeed(
        "seasonal",
        "Seasonal",
        (
            "Cozy autumn forest path covered in orange leaves",
            "Snow-covered cabin with warm windows on a winter night",
            "Spring meadow with cherry blossoms and soft sunlight",
            "Tropical beach at sunrise in summer with gentle waves",
        ),
    ),
)
