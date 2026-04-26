#!/usr/bin/env bash
# fetch.sh — Download Google Fonts referenced by preset-table.json.
#
# Drops .ttf files into the same directory as this script, where
# render-logo.py looks for them first. Idempotent: skips fonts that
# already exist. Run once after install, or any time the preset table
# adds a new font.
#
# Friends/ECW/WCW use TV/dafont-licensed fonts that can't be redistributed —
# their preset entries list Google Fonts approximations. To get the real
# look, manually drop the original TTFs into this folder with the filenames
# the presets reference.
#
# Compatible with bash 3.2 (macOS default) and bash 4+ — uses parallel
# arrays instead of `declare -A` so it works on stock macOS without
# requiring `brew install bash`.

set -eu
set -o pipefail

cd "$(dirname "$0")"

# Two parallel arrays: SLUGS[i] is the google/fonts/ofl/<slug> directory,
# FILES[i] is the TTF filename inside it. Adding a new genre = one row in
# each array. Order doesn't matter; just keep the indices aligned.
SLUGS=(
    abrilfatface archivoblack bangers bebasneue blackopsone bodoni bungee
    cinzeldecorative creepster dancingscript fredoka greatvibes lobstertwo
    luckiestguy metalmania mousememoirs nosifer orbitron pacifico
    playfairdisplay pressstart2p quicksand righteous rubikvinyl rye
    satisfy specialelite teko tiltneon tomorrow vt323
)
FILES=(
    AbrilFatface-Regular.ttf ArchivoBlack-Regular.ttf Bangers-Regular.ttf
    BebasNeue-Regular.ttf BlackOpsOne-Regular.ttf Bodoni-Regular.ttf
    Bungee-Regular.ttf CinzelDecorative-Bold.ttf Creepster-Regular.ttf
    DancingScript-Regular.ttf Fredoka-Regular.ttf GreatVibes-Regular.ttf
    LobsterTwo-Bold.ttf LuckiestGuy-Regular.ttf MetalMania-Regular.ttf
    MouseMemoirs-Regular.ttf Nosifer-Regular.ttf Orbitron-Regular.ttf
    Pacifico-Regular.ttf PlayfairDisplay-Regular.ttf PressStart2P-Regular.ttf
    Quicksand-Regular.ttf Righteous-Regular.ttf RubikVinyl-Regular.ttf
    Rye-Regular.ttf Satisfy-Regular.ttf SpecialElite-Regular.ttf
    Teko-Regular.ttf TiltNeon-Regular.ttf Tomorrow-Bold.ttf VT323-Regular.ttf
)

# google/fonts repo layout: ofl/<family>/<File>.ttf
BASE="https://raw.githubusercontent.com/google/fonts/main/ofl"

if [ "${#SLUGS[@]}" -ne "${#FILES[@]}" ]; then
    echo "ERROR: SLUGS and FILES arrays out of sync (${#SLUGS[@]} vs ${#FILES[@]})" >&2
    exit 2
fi

skipped=0
fetched=0
failed_count=0
failed_list=""

i=0
while [ "$i" -lt "${#SLUGS[@]}" ]; do
    slug="${SLUGS[$i]}"
    file="${FILES[$i]}"
    if [ -f "$file" ]; then
        skipped=$((skipped + 1))
    else
        url="${BASE}/${slug}/${file}"
        if curl -fsSL -o "$file" "$url"; then
            fetched=$((fetched + 1))
            printf "  fetched: %s\n" "$file"
        else
            failed_count=$((failed_count + 1))
            failed_list="${failed_list}
  - ${file} (${url})"
            rm -f "$file"
        fi
    fi
    i=$((i + 1))
done

echo
echo "fonts: $fetched fetched, $skipped already present, $failed_count failed"
if [ "$failed_count" -gt 0 ]; then
    printf "%s\n" "$failed_list"
    echo
    echo "Fonts that 404 here usually moved into google/fonts/apache/ or"
    echo "ufl/ instead of ofl/. Search the repo and adjust BASE for that one."
    exit 1
fi
