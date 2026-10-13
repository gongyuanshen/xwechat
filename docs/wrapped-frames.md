# Annual Summary Sharing Frame Research (2026-08)

When exporting an Annual Summary as a shareable image, the frame cannot be chosen arbitrarily: each platform handles uploaded image aspect ratios differently. Choosing the wrong ratio means **the platform crops the image for you**, often cutting off numbers and titles.

This document provides the basis for `FRAME_PRESETS` in `frontend/lib/wrapped-stage.js`.

## Implemented Frames: 6 Presets + "Follow Window"

| Frame | Export pixels | Platforms covered | Why it is needed |
|---|---|---|---|
| **9:16** | 1080×1920 | WeChat Moments single image · Douyin · Kuaishou · WeChat Channels · IG Stories/Reels · FB Stories · TikTok · YouTube Shorts | The widest coverage. One image serves 4 domestic and 4 international entry points |
| **3:4** | 1080×1440 | Xiaohongshu notes · Douyin image/text posts · Instagram Feed and profile grid | The primary domestic image/text use case. Xiaohongshu **allows only one ratio throughout a note**, and IG profile grids already crop to 3:4: **the only portrait format avoiding crops on both domestic and international platforms** |
| **4:5** | 1080×1350 | Instagram · Facebook · Threads · LinkedIn feeds | The standard portrait format that occupies the most space in international feeds without cropping. Visually close to 3:4 but **not interchangeable** |
| **1:1** | 1080×1080 | WeChat Moments nine-image grid · Weibo · Qzone · Bilibili posts · IG/X/LinkedIn square images | The baseline grid format. The four major domestic social-post platforms **center-crop sets of three or more images to 1:1**; without square images, the platform decides what to crop |
| **4:3** | 1440×1080 | Landscape image/text posts · Presentations | Greater information density than 16:9 among landscape formats, suitable for fitting a complete card on one screen |
| **16:9** | 1920×1080 | X timeline · Bilibili covers · Web sharing cards (OG image) | X center-crops portrait images to approximately 16:9 previews; only 16:9 displays at full width |
| Follow Window | — | Desktop browsing / Full-screen presentation | The default, with behavior identical to the version before the stage layout |

## Platform Safe Areas: Avoiding Overlays

- **9:16 has the tightest limits**: IG Stories uses approximately 250px at the top (avatar/progress bar) and 250px at the bottom. For the same image to work in Reels, leave approximately 420px at the bottom.
  TikTok uses approximately 150px at the top, 250–400px at the bottom, and **120px on the right** for the like/comment/share button column.
  Keep core content within the central **1080×1420**, with another 120px reserved on the right, to share one image across these destinations.
- **3:4 Xiaohongshu feed covers**: Approximately 150px at the top and 200px at the bottom are covered by title text and interaction buttons. Place core information in the upper central 1/3 of the image.
- **1:1**: Generally free of overlays and the easiest format to manage.
- **4:5**: No feed overlays, but the IG profile grid crops it again to 3:4; leave approximately 8% extra space at both top and bottom.
- **16:9**: No overlays on X. Link-preview cards may crop to 1.91:1 in some clients, so keep important elements away from the edges.

## File Size and Format

- Use sRGB throughout; data graphics dominated by text/lines should use PNG or high-quality JPG.
- Design each Xiaohongshu image for ≤10MB (both 10/20MB limits are reported; use the smaller value). Weibo: ≤5MB and a maximum upload width of 2048px.
  Public-account images: ≤5MB; Bilibili covers: ≤2MB; X mobile: 5MB.
- WeChat Moments heavily compresses images sent without "Original Image" (reducing the long edge to 1080–2200px). For long images containing text, select the original-image option.

## Two Formats Not Yet Implemented

- **Long image (1080×2400+, adaptive height)**: For Chinese users, sending a single long image to a WeChat group is a natural first choice and ranks among the top three in coverage.
  It requires stitching 8 cards vertically into one continuous canvas, a rendering model distinct from a slide deck, so it is a separate feature.
- **2:3 (1000×1500), Pinterest**: A single-platform format with almost no audience overlap with the other frames. It merits a preset only when explicitly targeting long-tail traffic in Europe and North America.
  Pinterest's hard limit matters: images longer than roughly 1:2.1 are cut off at the bottom and downranked. **Do not post long images to Pinterest.**

## Sources and Reliability

International specifications come from Hootsuite's "Social media image sizes for all networks" (updated 2026-08) and Buffer's "Social Media Image Sizes in 2026" (updated 2026-05). Stories/Reels safe areas come from Meta's consolidated advertising safe-area guidance from 2026-03; TikTok safe areas come from several 2026 specification guides.

⚠️ **Chinese platforms have no public developer specification documents.** The domestic-platform section uses third-party compilations from 2025–2026 and contains conflicting figures: public-account lead covers are reported as both 900×383 and 2360×1100; Xiaohongshu per-image limits as both 10MB and 20MB; and Bilibili covers as both 16:9 and 16:10. Before committing to exact pixel dimensions, perform an actual-device check for a single WeChat Moments image, a Xiaohongshu first image, and a Douyin image/text post.
