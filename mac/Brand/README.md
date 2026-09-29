# Brand

"Unwind": a spiral that straightens into a line. It stands for the thing
unrot is about: going from skimming a term to understanding it.

| File | What it is |
|---|---|
| `make_icons.py` | Renders the app icon into `../UnrotMac/Assets.xcassets/AppIcon.appiconset` |
| `app-icon.svg` | The 1024 px master it renders from (generated) |

```bash
uv run --with cairosvg --with pillow mac/Brand/make_icons.py
```

The mark is one path, in its own units:

```
M50 50  a5 5 0 0 1 10 0  a10 10 0 0 1 -20 0  a15 15 0 0 1 15 -15  H92
```

It is drawn three times, and the three use the same units:

- the app icon, here (moss `#3A6E53` → `#264B38`, paper `#F3EFE6`)
- the menu-bar template glyph, `UnrotMac/Tray/TrayGlyph.swift`
- the SwiftUI mark and wordmark, `UnrotMac/Brand/UnwindMark.swift`

Small sizes drop turns rather than shrink them: the 32 px icon keeps the outer
turn and the line, the 16 px keeps only the hook.

The icon is a classic `.appiconset` on Apple's grid (an 824 px rounded square
on a 1024 px canvas). macOS 26 can derive dark and tinted looks from an icon
like this on its own. A
layered Icon Composer `.icon` file, for hand-tuned dark, clear and tinted
versions, is not made yet.
