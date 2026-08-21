# Brand assets

`icon.svg` is the master; the PNGs are rendered from it. Everything here exists
to be submitted to [home-assistant/brands][brands], which is what puts a real
icon beside this integration instead of a placeholder. It is also a hard
requirement for the HACS default store.

## Rendering

Brands wants square images trimmed to the minimum empty space at the edges, so
the PNGs are not a straight export: render large, trim to the ink, square off,
then resize.

```sh
cairosvg icon.svg -o /tmp/hi.png --output-width 2048 --output-height 2048
magick /tmp/hi.png -trim +repage /tmp/trim.png
SIDE=$(magick identify -format "%[fx:max(w,h)]" /tmp/trim.png)
magick /tmp/trim.png -background none -gravity center -extent ${SIDE}x${SIDE} /tmp/sq.png
magick /tmp/sq.png -resize 256x256 -strip -define png:compression-level=9 icon.png
magick /tmp/sq.png -resize 512x512 -strip -define png:compression-level=9 'icon@2x.png'
```

`tests/test_packaging.py` checks both files are exactly 256x256 and 512x512, so
a forgotten re-render fails at home rather than in review.

## Submitting

Copy `icon.png` and `icon@2x.png` into `custom_integrations/pumpspy_local/` in a
fork of home-assistant/brands and open a pull request. Custom integrations must
not use Home Assistant's own branding, and this project must not use the
vendor's either: the mark is original, and no PumpSpy or PitBoss logo appears in
it.

## Why it looks like this

It is a sump pit in cross section. Water in the bottom, the pump sitting in it,
the discharge line carrying water away over the rim.

Three details were settled by drawing the alternative and watching it fail at
the size this is actually read, which is about 48 pixels in Home Assistant's
integrations list.

**The discharge stops short of the rim and elbows away above it.** Run full
height, the pipe turns the icon into a "W". Three verticals of equal length stop
reading as an object and start reading as a glyph.

**The pit is shallow rather than deep.** A deeper pit puts the elbow alongside
the rim, and at 48 pixels two strokes that close merge into a single blob.

**Everything is mid-toned on a transparent ground.** Home Assistant renders this
on light and dark themes both. Anything near-white or near-black has to pick one
and lose on the other; slate and blue hold on either, which is why there is no
background tile.

Rejected along the way, in case they look tempting later: a float switch on its
rod, which reads as a buoy; a pump and pipe assembly drawn without the pit
around it, which reads as a kitchen faucet; and a battery with a waterline in
it, which is legible but says flooded battery, the opposite of the point.

[brands]: https://github.com/home-assistant/brands
