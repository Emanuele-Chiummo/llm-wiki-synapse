#!/bin/bash

# Generate placeholder PNG icons for Tauri desktop app
# Uses ImageMagick if available; otherwise provides instructions

ICON_DIR="$(dirname "$0")"
COLORS="-fill '#2563eb' -draw 'rectangle 0,0 999,999' -fill white -pointsize 480 -gravity center -annotate 0 S"

if command -v convert &> /dev/null; then
    echo "Generating Tauri app icons..."

    # Generate PNG icons (32, 128, 256)
    for size in 32 128 256; do
        convert -size ${size}x${size} xc: $COLORS \
            "$ICON_DIR/icon-${size}x${size}.png" || echo "Failed to generate icon-${size}x${size}.png"
    done

    # Generate ICO (Windows) - use 32x32 as the base
    convert "$ICON_DIR/icon-32x32.png" -colors 256 "$ICON_DIR/icon.ico" 2>/dev/null || \
        echo "Failed to generate icon.ico"

    # Generate ICNS (macOS) - would need 1024x1024 source; using 256 as fallback
    convert "$ICON_DIR/icon-256x256.png" "$ICON_DIR/icon.icns" 2>/dev/null || \
        echo "Note: icon.icns generation requires macOS tooling; manually create or use online converter"

    echo "Icons generated in $ICON_DIR"
else
    echo "ImageMagick not installed. Placeholder icons can be generated at:"
    echo "  https://www.photopea.com/ or similar online editor"
    echo ""
    echo "Recommended sizes:"
    echo "  - icon-32x32.png (favicon / small icon)"
    echo "  - icon-128x128.png (system tray on Linux)"
    echo "  - icon-256x256.png (app launcher icon)"
    echo "  - icon.ico (Windows taskbar)"
    echo "  - icon.icns (macOS dock)"
    echo ""
    echo "All should be placed in: $ICON_DIR"
fi
