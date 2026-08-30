"""Canonical identity normalization shared by release and repository gates."""

from __future__ import annotations

import unicodedata


# Complete Unicode Default_Ignorable_Code_Point ranges.  Keep this explicit so
# runtime validation remains standard-library-only and does not over-normalize
# visible format controls that are not members of the property.
DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def is_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return any(
        start <= codepoint <= end
        for start, end in DEFAULT_IGNORABLE_RANGES
    )


def normalized_actor_identity(value: str) -> str:
    """Return a comparison identity with invisible distinctions removed."""

    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value)
        if not is_default_ignorable(character)
    ).strip().casefold()
