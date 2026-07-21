# Optional owned-image shot list and rights log

The public page now uses metadata-free WebP derivatives of official Pollen Robotics Apache-2.0 imagery plus project-authored explanatory SVGs; their immutable sources and changes are recorded in [IMAGE_CREDITS.md](IMAGE_CREDITS.md). The public set therefore no longer depends on stock imagery or generated mockups.

Tim-owned photos remain valuable for documenting the exact external-Pi reference build and real app UI, but they are optional follow-up evidence. Do not imply that official imagery shows Tim's custom Pi arrangement.

## Optional follow-up shots

| ID | Deliverable | Exact shot | Crop / target | Privacy and safety check | Rights status |
|---|---|---|---|---|---|
| `hero-reachy-owned` | Optional replacement hero | Tim's actual Reachy in a clean, well-lit three-quarter view, safely folded or Awake with clear space; companion hardware may be visible but no screen text | Landscape 16:9, minimum 1600×900; export WebP around 200–350 KB | No faces, house numbers, family photos, notifications, labels, serials, screens or location clues | **Optional: Tim-owned source required** |
| `lite-pi-overview` | Annotated setup | Powered-off Reachy Mini Lite and Pi 4 in their real final positions, showing separate power leads, USB data cable, ventilation and motor-clearance envelope | Landscape 3:2, minimum 1800×1200; keep margin for numbered callouts | Remove PSU serials/MAC labels; do not stage unsafe shared power or cable routing | **Blocked: Tim-owned source + confirmed build facts required** |
| `ui-dashboard` | App screenshot | Dashboard showing Standby, folded/torque-released state and privacy controls | 1440×900 desktop or a clean responsive crop | Use synthetic/non-private transcript text or blank state; redact host/address/status identifiers | **Captured with synthetic status; see IMAGE_CREDITS.md** |
| `ui-robot` | App screenshot | Robot tab with bounded look/Stop controls and camera disabled | 1440×900 | Camera preview off; no controller MAC, local address or device name | **Captured with synthetic status; see IMAGE_CREDITS.md** |
| `ui-kids` | App screenshot | Kids activity selector and parent-facing safeguards; no active child session | 1440×900 | No child nickname, PIN, transcript or face | **Optional follow-up** |
| `use-case` | Supporting photo | One real, representative interaction such as voice conversation from behind the person or hands-only controller use | 4:3 or 3:2, minimum 1400 px wide | No identifiable face without explicit consent; controller use must match validated hardware scope | **Blocked: Tim-owned source required** |

## Annotation plan for the Lite + Pi overview

Use only callouts proven by the photographed build:

1. Reachy supplied PSU — robot power only.
2. USB data link — Reachy Lite to Raspberry Pi 4.
3. Raspberry Pi-rated PSU — Pi power only.
4. Raspberry Pi 4 companion host — exact model/OS in caption after confirmation.
5. Ventilation zone — keep unobstructed.
6. Cable strain relief — attached to stationary support.
7. Motor/base clearance — no cable or mount may enter this area.

Do not label the arrangement “Wireless conversion.” Caption it “Community Lite + Raspberry Pi 4 companion-host reference build.”

## Sanitization and export workflow

Work from copies, never the originals.

1. Inspect the entire frame at 100%, including reflections, browser chrome and background screens.
2. Remove or crop private content. Do not rely on blur for tokens, QR codes or serials; crop or paint them out completely.
3. Strip metadata, including EXIF, GPS, device and editing history. Example after installing ExifTool:

   ```bash
   exiftool -all= -overwrite_original public-image.webp
   exiftool -G1 -a -s public-image.webp
   ```

4. Export responsive WebP (or AVIF plus WebP fallback) in sRGB. Keep UI screenshots sharp; use lossless WebP if text artifacts appear.
5. Record source owner, capture date, edits, export dimensions, checksum and approval in the rights log below.
6. Open every final file and inspect it visually. Then render both the GitHub README and Space at desktop and mobile widths and inspect crops, captions, alt text and contrast.

## Rights and verification log

Complete this before publishing any Tim-owned additions. Official-source and project-authored files are logged in [IMAGE_CREDITS.md](IMAGE_CREDITS.md).

| Public file | Source owner / permission | Sanitization | Metadata check | Visual inspection | Approved by |
|---|---|---|---|---|---|
| `docs/assets/hero-reachy-owned.webp` | TBD | TBD | TBD | TBD | TBD |
| `docs/assets/lite-pi-overview.webp` | TBD | TBD | TBD | TBD | TBD |
| `docs/assets/ui-dashboard-owned.webp` | TBD | TBD | TBD | TBD | TBD |
| `docs/assets/ui-robot-owned.webp` | TBD | TBD | TBD | TBD | TBD |
| `docs/assets/ui-kids.webp` | TBD | TBD | TBD | TBD | TBD |
| `docs/assets/use-case.webp` | TBD | TBD | TBD | TBD | TBD |

The generated `docs/assets/architecture.svg` is project-authored vector artwork containing no third-party raster imagery or metadata. It is explanatory, not evidence of physical hardware acceptance.
