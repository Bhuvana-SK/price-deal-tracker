"""Chart colours: the validated reference palette, with separate light and dark steps.

Series colours are assigned to entities in a fixed order and never cycled; a
9th+ series falls back to muted grey. "Genuine deal" is a state, so it uses
the reserved status-good colour and always ships with a label.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    """Every colour role the dashboard uses."""

    mode: str
    surface: str
    text_primary: str
    text_secondary: str
    muted: str
    grid: str
    baseline: str
    series: tuple[str, ...]
    good: str

    def series_colors(self, n: int) -> list[str]:
        """Colours for n series in fixed slot order (extras become muted)."""
        return [self.series[i] if i < len(self.series) else self.muted for i in range(n)]


LIGHT = Palette(
    mode="light",
    surface="#fcfcfb",
    text_primary="#0b0b0b",
    text_secondary="#52514e",
    muted="#898781",
    grid="#e1e0d9",
    baseline="#c3c2b7",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"),
    good="#0ca30c",
)

DARK = Palette(
    mode="dark",
    surface="#1a1a19",
    text_primary="#f0efec",
    text_secondary="#c3c2b7",
    muted="#898781",
    grid="#2c2c2a",
    baseline="#383835",
    series=("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"),
    good="#0ca30c",
)


def palette_for(theme_type: str | None) -> Palette:
    """Pick the palette for Streamlit's active theme ('light' / 'dark')."""
    return DARK if theme_type == "dark" else LIGHT
