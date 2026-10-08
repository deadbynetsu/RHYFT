# Public upstream renderer excerpts

These are minimized **captured browse response rows**, not fabricated renderer
examples and **not captured search responses**. Their source is
[`sigma67/ytmusicapi`](https://github.com/sigma67/ytmusicapi) at the pinned commit
[`4aeaf7d0aa48e3fb56eb229ec04593655acba091`](https://github.com/sigma67/ytmusicapi/tree/4aeaf7d0aa48e3fb56eb229ec04593655acba091).
The month in each filename is the upstream fixture label; it is not a claim
about when this environment connected to Google.

| Local excerpt | Upstream capture and JSON path | Preserved shape |
| --- | --- | --- |
| `upstream-2024-03-album-rows.json` | `tests/data/2024_03_get_album.json`; `contents.twoColumnBrowseResultsRenderer.secondaryContents.sectionListRenderer.contents[0].musicShelfRenderer.contents[0:2]` | Two ATV rows; title watch endpoints and thumbnail overlay endpoints; fixed-column durations; no artist metadata in these particular album rows. |
| `upstream-2026-05-album-video-rows.json` | `tests/data/2026_05_get_album.json`; same path as above | Two OMV rows; flex artist links; fixed-column durations. These are videos from an album capture, so a songs filter should exclude them. |
| `upstream-2026-05-artist-song-rows.json` | `tests/data/2026_05_get_artist1.json`; `contents.singleColumnBrowseResultsRenderer.tabs[0].tabRenderer.content.sectionListRenderer.contents[0].musicShelfRenderer.contents[0:2]` | Two ATV rows; Unicode titles and multiple linked artists; additional album/view metadata; no duration in these particular rows. |

Only the six selected responsive rows were copied. Field names and retained
values are preserved from those rows. Menus, thumbnails, accessibility text,
tracking data, logging context, feedback tokens, player parameters, playlist
IDs/set-video IDs, and other unrelated fields were removed. Retained IDs are
public catalogue video, artist and album IDs. No cookies, account credentials,
visitor values, integrity data, authorization headers or user session data are
included.

The JSON files contain arrays of `musicResponsiveListItemRenderer` wrappers.
A test can insert those arrays into a `musicShelfRenderer` inside a
`sectionListRenderer`, optionally inside `tabbedSearchResultsRenderer`, to
exercise search parsing. **Such an enclosing search response is synthetic**:
these source files establish real row structure, not a live search envelope.
They cannot establish the cause of a production bootstrap or search error.

No real bootstrap HTML or raw search response was found in that pinned
upstream tree. Its `tests/parsers/test_search.py` uses constructed rows, while
`tests/mixins/test_search.py` performs live searches. A separate passive audit
of [`LuanRT/YouTube.js` at
`bad89d2657e88f907011655f199fba9fb615c339`](https://github.com/LuanRT/YouTube.js/tree/bad89d2657e88f907011655f199fba9fb615c339)
also found live tests rather than checked-in Music search/bootstrap captures.
Its parser source supports `musicCardShelfRenderer` with `title`, `subtitle`,
`onTap`, `thumbnailOverlay` and optional nested `contents`; that is additional
source evidence, not an unmodified captured fixture.

## Upstream license

MIT License

Copyright (c) 2026 sigma67

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
