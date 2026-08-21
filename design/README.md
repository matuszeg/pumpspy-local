# Icon source

`icon.svg` is the master. The PNGs it renders to live in
`custom_components/pumpspy_local/brand/`, which is where Home Assistant looks
for an integration's own brand images.

## Where the images go, and why not home-assistant/brands

They used to go there. As of **Home Assistant 2026.3** a custom integration ships
its own icons instead, in a `brand/` directory beside `manifest.json`, and
[home-assistant/brands no longer accepts custom integrations][announcement] —
a pull request adding one is closed automatically. Local images take priority
over the brands CDN and need no manifest key.

```
custom_components/pumpspy_local/
├── manifest.json
└── brand/
    ├── icon.png       256x256
    └── icon@2x.png    512x512
```

Anyone on Home Assistant older than 2026.3 sees the usual placeholder. That is
the only cost, and it is not worth raising this integration's minimum version
over, so `hacs.json` still allows 2024.6.

Supported names, should a logo ever be wanted: `icon.png`, `logo.png`, their
`@2x` variants, and `dark_` prefixed versions of each. Only the two icons exist
here; the mark is legible on light and dark alike, so a dark variant would be
two more files to keep in sync for no gain.

## Rendering

The PNGs are not a straight export. Images should be square and trimmed to the
minimum empty space at the edges, so: render large, trim to the ink, square off,
then resize.

```sh
cd design
cairosvg icon.svg -o /tmp/hi.png --output-width 2048 --output-height 2048
magick /tmp/hi.png -trim +repage /tmp/trim.png
SIDE=$(magick identify -format "%[fx:max(w,h)]" /tmp/trim.png)
magick /tmp/trim.png -background none -gravity center -extent ${SIDE}x${SIDE} /tmp/sq.png
OUT=../custom_components/pumpspy_local/brand
magick /tmp/sq.png -resize 256x256 -strip -define png:compression-level=9 $OUT/icon.png
magick /tmp/sq.png -resize 512x512 -strip -define png:compression-level=9 "$OUT/icon@2x.png"
```

`tests/test_packaging.py` checks both files are exactly 256x256 and 512x512, so
a forgotten re-render fails at home rather than in someone's Home Assistant.

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

[announcement]: https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api
