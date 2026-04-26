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

set -euo pipefail

cd "$(dirname "$0")"

# Google Fonts release tarballs the TTF directly off the github CDN.
# Family-slug -> filename(s). Listed here so adding a new genre = one line.
declare -A FONTS=(
    ["abrilfatface"]="AbrilFatface-Regular.ttf"
    ["archivoblack"]="ArchivoBlack-Regular.ttf"
    ["bangers"]="Bangers-Regular.ttf"
    ["bebasneue"]="BebasNeue-Regular.ttf"
    ["blackopsone"]="BlackOpsOne-Regular.ttf"
    ["bodoni"]="Bodoni-Regular.ttf"
    ["bungee"]="Bungee-Regular.ttf"
    ["cinzeldecorative"]="CinzelDecorative-Bold.ttf"
    ["creepster"]="Creepster-Regular.ttf"
    ["dancingscript"]="DancingScript-Regular.ttf"
    ["fredoka"]="Fredoka-Regular.ttf"
    ["greatvibes"]="GreatVibes-Regular.ttf"
    ["lobstertwo"]="LobsterTwo-Bold.ttf"
    ["luckiestguy"]="LuckiestGuy-Regular.ttf"
    ["metalmania"]="MetalMania-Regular.ttf"
    ["mousememoirs"]="MouseMemoirs-Regular.ttf"
    ["nosifer"]="Nosifer-Regular.ttf"
    ["orbitron"]="Orbitron-Regular.ttf"
    ["pacifico"]="Pacifico-Regular.ttf"
    ["playfairdisplay"]="PlayfairDisplay-Regular.ttf"
    ["pressstart2p"]="PressStart2P-Regular.ttf"
    ["quicksand"]="Quicksand-Regular.ttf"
    ["righteous"]="Righteous-Regular.ttf"
    ["rubikvinyl"]="RubikVinyl-Regular.ttf"
    ["rye"]="Rye-Regular.ttf"
    ["satisfy"]="Satisfy-Regular.ttf"
    ["specialelite"]="SpecialElite-Regular.ttf"
    ["teko"]="Teko-Regular.ttf"
    ["tiltneon"]="TiltNeon-Regular.ttf"
    ["tomorrow"]="Tomorrow-Bold.ttf"
    ["vt323"]="VT323-Regular.ttf"
)

# google/fonts repo layout: ofl/<family>/<File>.ttf
BASE="https://raw.githubusercontent.com/google/fonts/main/ofl"

skipped=0
fetched=0
failed=()

for slug in "${!FONTS[@]}"; do
    file="${FONTS[$slug]}"
    if [[ -f "$file" ]]; then
        skipped=$((skipped + 1))
        continue
    fi
    url="${BASE}/${slug}/${file}"
    if curl -fsSL -o "$file" "$url"; then
        fetched=$((fetched + 1))
        printf "  fetched: %s\n" "$file"
    else
        failed+=("$file ($url)")
        rm -f "$file"
    fi
done

echo
echo "fonts: $fetched fetched, $skipped already present, ${#failed[@]} failed"
if [[ ${#failed[@]} -gt 0 ]]; then
    printf "  - %s\n" "${failed[@]}"
    echo
    echo "Fonts that 404 here usually moved into google/fonts/apache/ or"
    echo "ufl/ instead of ofl/. Search the repo and adjust BASE for that one."
    exit 1
fi
