// Renders the app icon and the DMG background in the labyrinth style of ui/index.html.
//
//   swift scripts/make_art.swift OUT_DIR
//
// writes OUT_DIR/AppIcon.iconset/ (the ten sizes 16-1024) and OUT_DIR/background.png
// and background@2x.png (660x400 and 1320x800). Then, from the repo root:
//
//   iconutil -c icns OUT_DIR/AppIcon.iconset -o app/AppIcon.icns
//   tiffutil -cathidpicheck OUT_DIR/background.png OUT_DIR/background@2x.png -out app/dmg/background.tiff
//
// The outputs are committed, so builds never run this. Every size is drawn natively
// (nothing is downscaled): the small sizes get fewer rings, thicker lines and no bezel.
import AppKit
import CoreText
import ImageIO
import UniformTypeIdentifiers

// MARK: - Palette (the page's tokens)

let ink = CGColor(srgbRed: 0x12 / 255, green: 0x11 / 255, blue: 0x10 / 255, alpha: 1)
let inkLift = CGColor(srgbRed: 0x1d / 255, green: 0x1b / 255, blue: 0x19 / 255, alpha: 1)  // --raise
func parchment(_ a: CGFloat) -> CGColor { CGColor(srgbRed: 0xec / 255, green: 0xe5 / 255, blue: 0xd8 / 255, alpha: a) }
func brass(_ a: CGFloat = 1) -> CGColor { CGColor(srgbRed: 0xd4 / 255, green: 0xa2 / 255, blue: 0x4c / 255, alpha: a) }

let srgb = CGColorSpace(name: CGColorSpace.sRGB)!

/// Parchment at alpha a over ink, as an opaque colour.
func onInk(_ a: CGFloat) -> CGColor {
    func mix(_ p: CGFloat, _ i: CGFloat) -> CGFloat { (p * a + i * (1 - a)) / 255 }
    return CGColor(srgbRed: mix(0xec, 0x12), green: mix(0xe5, 0x11), blue: mix(0xd8, 0x10), alpha: 1)
}

// MARK: - Geometry: angles are degrees clockwise from 12 o'clock, as in the page's maze.

func polar(_ c: CGPoint, _ r: CGFloat, _ deg: CGFloat, yDown: Bool = false) -> CGPoint {
    let a = deg * .pi / 180
    return CGPoint(x: c.x + r * sin(a), y: yDown ? c.y - r * cos(a) : c.y + r * cos(a))
}

/// An arc along radius r from `from` to `to` (degrees, either direction), as a polyline
/// fine enough to be smooth at any size we render.
func addArc(_ p: CGMutablePath, _ c: CGPoint, _ r: CGFloat, _ from: CGFloat, _ to: CGFloat,
            move: Bool, yDown: Bool = false) {
    let steps = max(2, Int(abs(to - from) / 0.5))
    for i in 0...steps {
        let pt = polar(c, r, from + (to - from) * CGFloat(i) / CGFloat(steps), yDown: yDown)
        if i == 0 && move { p.move(to: pt) } else { p.addLine(to: pt) }
    }
}

func norm(_ a: CGFloat) -> CGFloat { (a.truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360) }

/// Arcs of a ring of radius r with the given gaps cut out. Gaps are (centre, width) in degrees.
func ringPath(_ c: CGPoint, _ r: CGFloat, gaps: [(CGFloat, CGFloat)], yDown: Bool = false) -> CGMutablePath {
    let p = CGMutablePath()
    let g = gaps.map { (norm($0.0), $0.1) }.sorted { $0.0 < $1.0 }
    if g.isEmpty { addArc(p, c, r, 0, 360, move: true, yDown: yDown); return p }
    for i in 0..<g.count {
        let from = g[i].0 + g[i].1 / 2
        var to = g[(i + 1) % g.count].0 - g[(i + 1) % g.count].1 / 2
        if i == g.count - 1 || to <= from { to += 360 }
        if to - from < 1 { continue }
        addArc(p, c, r, from, to, move: true, yDown: yDown)
    }
    return p
}

// MARK: - The icon's maze, one design per tier

/// A corridor run of the brass path: along radius r from `from` to `to` degrees.
struct Run { let r: CGFloat; let from: CGFloat; let to: CGFloat }

struct Tier {
    let walls: [CGFloat]                 // ring radii, core first, in 1024-units
    let wallWidth: CGFloat               // in device pixels
    let pathWidth: CGFloat               // in device pixels
    let dotRadius: CGFloat               // in device pixels
    let entry: CGFloat                   // angle of the way in
    let pathStart: CGFloat               // radius the brass path starts at
    let runs: [Run]                      // outermost corridor first
    let reachesCentre: Bool              // false: the path stops in the last corridor
    let decoys: [Int: [CGFloat]]         // extra openings per wall index
    let crossWalls: [(Int, CGFloat)]     // (corridor index = inner wall index, angle)
    let door: CGFloat                    // opening width, in 1024-units
    let bezel: Bool
    let wallAlpha: CGFloat
    let edgeAlpha: CGFloat               // the outermost wall
}

let fullTier = Tier(
    walls: [84, 146, 208, 270, 332], wallWidth: 0, pathWidth: 0, dotRadius: 0,
    entry: 205, pathStart: 372,
    runs: [Run(r: 301, from: 205, to: 262), Run(r: 239, from: 262, to: 338),
           Run(r: 177, from: 338, to: 410), Run(r: 115, from: 410, to: 292)],
    reachesCentre: true,
    decoys: [4: [48], 3: [100, 172], 2: [20, 196], 1: [138, 236]],
    crossWalls: [(3, 120), (3, 18), (2, 72), (2, 182), (1, 110), (1, 230), (0, 200)],
    door: 54, bezel: true, wallAlpha: 0.70, edgeAlpha: 0.84)

func tier(forPixels s: Int) -> Tier {
    let u = 1024 / CGFloat(s)                        // 1024-units per device pixel
    switch s {
    case ..<24:                                      // 16: two rings and the way straight in
        return Tier(walls: [168, 330], wallWidth: 1.2, pathWidth: 1.4, dotRadius: 1.25,
                    entry: 225, pathStart: 420,
                    runs: [Run(r: 250, from: 225, to: 225)], reachesCentre: true,
                    decoys: [0: [60], 1: [100]], crossWalls: [], door: 1.9 * u, bezel: false,
                    wallAlpha: 0.80, edgeAlpha: 0.86)
    case ..<48:                                      // 32: three rings, one turn, then straight in
        return Tier(walls: [112, 232, 352], wallWidth: 1.3, pathWidth: 1.7, dotRadius: 1.6,
                    entry: 215, pathStart: 410,
                    runs: [Run(r: 292, from: 215, to: 300)], reachesCentre: true,
                    decoys: [2: [60], 1: [140]], crossWalls: [], door: 2.3 * u, bezel: false,
                    wallAlpha: 0.76, edgeAlpha: 0.84)
    case ..<96:                                      // 64: four rings
        return Tier(walls: [104, 184, 264, 344], wallWidth: 1.6, pathWidth: 2.1, dotRadius: 2.2,
                    entry: 205, pathStart: 400,
                    runs: [Run(r: 304, from: 205, to: 262), Run(r: 224, from: 262, to: 410),
                           Run(r: 144, from: 410, to: 292)], reachesCentre: true,
                    decoys: [3: [100], 2: [170], 1: [200]], crossWalls: [(2, 120), (1, 160)], door: 2.6 * u,
                    bezel: false, wallAlpha: 0.72, edgeAlpha: 0.84)
    default:                                         // 128 and up: the whole maze
        let px = CGFloat(s) / 1024
        return Tier(walls: fullTier.walls, wallWidth: max(1.6, 13 * px), pathWidth: max(2.2, 18 * px),
                    dotRadius: max(2.6, 21 * px), entry: fullTier.entry, pathStart: fullTier.pathStart,
                    runs: fullTier.runs, reachesCentre: true, decoys: fullTier.decoys,
                    crossWalls: fullTier.crossWalls, door: max(fullTier.door, 3.2 * u), bezel: true,
                    wallAlpha: fullTier.wallAlpha, edgeAlpha: fullTier.edgeAlpha)
    }
}

// MARK: - Drawing helpers

func makeContext(_ w: Int, _ h: Int, opaque: Bool) -> CGContext {
    CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0, space: srgb,
              bitmapInfo: opaque ? CGImageAlphaInfo.noneSkipLast.rawValue
                                 : CGImageAlphaInfo.premultipliedLast.rawValue)!
}

func writePNG(_ ctx: CGContext, _ url: URL, dpi: CGFloat = 72) {
    let img = ctx.makeImage()!
    let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)!
    let props: [CFString: Any] = [kCGImagePropertyDPIWidth: dpi, kCGImagePropertyDPIHeight: dpi]
    CGImageDestinationAddImage(dest, img, props as CFDictionary)
    guard CGImageDestinationFinalize(dest) else { fatalError("could not write \(url.path)") }
}

/// A seeded, very faint paper grain (the page's --grain), as an image to draw over a fill.
func grain(_ w: Int, _ h: Int, seed: UInt32, alpha: UInt8) -> CGImage {
    var s = seed
    func next() -> UInt32 {                          // mulberry32, as the page's maze uses
        s = s &+ 0x6D2B79F5
        var t = (s ^ (s >> 15)) &* (1 | s)
        t = (t &+ ((t ^ (t >> 7)) &* (61 | t))) ^ t
        return t ^ (t >> 14)
    }
    var px = [UInt8](repeating: 0, count: w * h * 4)
    for i in 0..<(w * h) {
        let a = UInt8(UInt32(alpha) * (next() & 0xff) / 255)
        let f = CGFloat(a) / 255                     // premultiplied parchment
        px[i * 4] = UInt8(0xec * f); px[i * 4 + 1] = UInt8(0xe5 * f); px[i * 4 + 2] = UInt8(0xd8 * f)
        px[i * 4 + 3] = a
    }
    let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                        space: srgb, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    return ctx.makeImage()!
}

/// Apple's icon body: a continuous-corner square (a superellipse, n = 5), centred.
func squircle(_ c: CGPoint, half: CGFloat) -> CGPath {
    let p = CGMutablePath()
    let n: CGFloat = 5, steps = 720
    for i in 0..<steps {
        let t = CGFloat(i) / CGFloat(steps) * 2 * .pi
        let ct = cos(t), st = sin(t)
        let x = half * (ct < 0 ? -1 : 1) * pow(abs(ct), 2 / n)
        let y = half * (st < 0 ? -1 : 1) * pow(abs(st), 2 / n)
        let pt = CGPoint(x: c.x + x, y: c.y + y)
        if i == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
    }
    p.closeSubpath()
    return p
}

// MARK: - Icon

func renderIcon(pixels s: Int, to url: URL) {
    let ctx = makeContext(s, s, opaque: false)
    let k = CGFloat(s) / 1024                        // device pixels per 1024-unit
    let u = 1 / k
    ctx.scaleBy(x: k, y: k)
    let t = tier(forPixels: s)
    let c = CGPoint(x: 512, y: 512)

    // The body. Small sizes snap to whole pixels so the edge stays crisp.
    var half: CGFloat = 412
    if s <= 64 { half = CGFloat(s == 16 ? 7 : (s == 32 ? 13 : 26)) * u }
    let body = squircle(c, half: half)

    if s >= 128 {                                    // Apple's template shadow, inside the margin
        ctx.saveGState()
        ctx.setShadow(offset: CGSize(width: 0, height: -10 * k), blur: 22 * k, color: CGColor(gray: 0, alpha: 0.42))
        ctx.addPath(body); ctx.setFillColor(ink); ctx.fillPath()
        ctx.restoreGState()
    }

    ctx.saveGState()
    ctx.addPath(body); ctx.clip()
    ctx.setFillColor(ink); ctx.fill(CGRect(x: 0, y: 0, width: 1024, height: 1024))
    // A low lamp over the plate: the centre lifts towards --raise, the corners stay ink.
    let lamp = CGGradient(colorsSpace: srgb, colors: [inkLift, ink] as CFArray, locations: [0, 1])!
    ctx.drawRadialGradient(lamp, startCenter: CGPoint(x: 512, y: 600), startRadius: 0,
                           endCenter: CGPoint(x: 512, y: 560), endRadius: half * 1.25, options: [.drawsAfterEndLocation])
    if s >= 256 {
        ctx.saveGState(); ctx.scaleBy(x: u, y: u)    // grain at device resolution
        ctx.draw(grain(s, s, seed: 4242, alpha: 22), in: CGRect(x: 0, y: 0, width: s, height: s))
        ctx.restoreGState()
    }
    ctx.restoreGState()

    // The bezel: a hairline and a degree scale, as on a chart (big sizes only).
    if t.bezel {
        let rb: CGFloat = 368
        ctx.setLineCap(.butt)
        ctx.setStrokeColor(parchment(0.16)); ctx.setLineWidth(max(1, 3 * k) * u)
        ctx.addEllipse(in: CGRect(x: c.x - rb, y: c.y - rb, width: 2 * rb, height: 2 * rb)); ctx.strokePath()
        for a in stride(from: 0, to: 360, by: 10) {
            let major = a % 30 == 0
            let p1 = polar(c, rb, CGFloat(a)), p2 = polar(c, rb - (major ? 20 : 11), CGFloat(a))
            ctx.setStrokeColor(parchment(major ? 0.34 : 0.17)); ctx.setLineWidth(max(1, (major ? 4 : 3) * k) * u)
            ctx.move(to: p1); ctx.addLine(to: p2); ctx.strokePath()
        }
    }

    // Doors: wherever the path crosses a ring.
    var gaps = [Int: [(CGFloat, CGFloat)]]()
    func doorWidth(_ r: CGFloat) -> CGFloat { t.door / r * 180 / .pi }
    var crossings: [(CGFloat, CGFloat, CGFloat)] = [(t.entry, t.pathStart, t.runs[0].r)]
    for i in 1..<t.runs.count { crossings.append((t.runs[i - 1].to, t.runs[i - 1].r, t.runs[i].r)) }
    if t.reachesCentre { crossings.append((t.runs.last!.to, t.runs.last!.r, 0)) }
    for (a, r0, r1) in crossings {
        for (i, r) in t.walls.enumerated() where r < max(r0, r1) && r > min(r0, r1) {
            gaps[i, default: []].append((a, doorWidth(r)))
        }
    }
    for (i, angles) in t.decoys { for a in angles { gaps[i, default: []].append((a, doorWidth(t.walls[i]) * 0.9)) } }

    // Walls are parchment at ~70% over ink, pre-mixed and opaque, so the joins of rings
    // and cross walls don't stack into brighter dots.
    ctx.setLineCap(.round); ctx.setLineJoin(.round)
    ctx.setLineWidth(t.wallWidth * u)
    for (i, r) in t.walls.enumerated() {
        ctx.setStrokeColor(onInk(i == t.walls.count - 1 ? t.edgeAlpha : t.wallAlpha))
        ctx.addPath(ringPath(c, r, gaps: gaps[i] ?? [])); ctx.strokePath()
    }
    ctx.setStrokeColor(onInk(t.wallAlpha))
    for (j, a) in t.crossWalls {
        ctx.move(to: polar(c, t.walls[j], a)); ctx.addLine(to: polar(c, t.walls[j + 1], a)); ctx.strokePath()
    }

    // The way in, in brass: from outside the maze along each corridor to the centre.
    let path = CGMutablePath()
    path.move(to: polar(c, t.pathStart, t.entry))
    for run in t.runs {
        path.addLine(to: polar(c, run.r, run.from))
        addArc(path, c, run.r, run.from, run.to, move: false)
    }
    if t.reachesCentre { path.addLine(to: c) }
    ctx.setStrokeColor(brass()); ctx.setLineWidth(t.pathWidth * u)
    ctx.addPath(path); ctx.strokePath()
    ctx.setFillColor(brass())
    let dr = t.dotRadius * u
    ctx.fillEllipse(in: CGRect(x: c.x - dr, y: c.y - dr, width: 2 * dr, height: 2 * dr))

    // A hairline edge, so the ink body holds on a dark Dock or wallpaper.
    ctx.saveGState()
    ctx.addPath(body); ctx.clip()
    ctx.addPath(body); ctx.setStrokeColor(parchment(s <= 32 ? 0.20 : 0.13))
    ctx.setLineWidth(max(1, 4 * k) * u * 2); ctx.strokePath()   // half of it lies inside the clip
    ctx.restoreGState()

    writePNG(ctx, url)
}

// MARK: - DMG background (660 x 400 points, app at (165, 200), Applications at (495, 200))

func serifFont(_ size: CGFloat, italic: Bool) -> CTFont {
    var d = NSFont.systemFont(ofSize: size).fontDescriptor.withDesign(.serif)!
    if italic { d = d.withSymbolicTraits(.italic) }
    return NSFont(descriptor: d, size: size)! as CTFont
}

func renderBackground(scale: Int, to url: URL) {
    let W = 660, H = 400
    let ctx = makeContext(W * scale, H * scale, opaque: true)
    let k = CGFloat(scale)
    ctx.scaleBy(x: k, y: k)

    // Ink, with the same low lamp, centred on the app, and the paper grain.
    ctx.setFillColor(ink); ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
    let app = CGPoint(x: 165, y: 200), dest = CGPoint(x: 495, y: 200)   // y is the same either way up
    let lamp = CGGradient(colorsSpace: srgb, colors: [CGColor(srgbRed: 0x1a / 255, green: 0x18 / 255, blue: 0x16 / 255, alpha: 1), ink] as CFArray,
                          locations: [0, 1])!
    ctx.drawRadialGradient(lamp, startCenter: app, startRadius: 0, endCenter: app, endRadius: 330, options: [.drawsAfterEndLocation])
    ctx.saveGState(); ctx.scaleBy(x: 1 / k, y: 1 / k)
    ctx.draw(grain(W * scale, H * scale, seed: 1729, alpha: 9), in: CGRect(x: 0, y: 0, width: W * scale, height: H * scale))
    ctx.restoreGState()

    // The labyrinth around the app, fading out to the right. Its openings line up at
    // 90 degrees, where the brass line leaves for Applications.
    // (radius, openings as (angle, width in points)). The inner ring also opens under the
    // app, where Finder writes its name.
    let door: CGFloat = 26
    let rings: [(CGFloat, [(CGFloat, CGFloat)])] = [
        (98, [(90, door), (180, 92)]), (126, [(90, door), (330, door)]),
        (154, [(90, door), (150, door), (260, door)]), (182, [(90, door), (20, door)]),
        (210, [(90, door), (225, door), (300, door)]), (238, [(90, door), (120, door)]),
        (266, [(90, door), (340, door), (200, door)]), (294, [(90, door), (250, door)]),
    ]
    // Fade: full on the left, gone before the middle of the window (a grey mask: white paints).
    let fade = CGContext(data: nil, width: W * scale, height: H * scale, bitsPerComponent: 8, bytesPerRow: 0,
                         space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)!
    let fg = CGGradient(colorSpace: CGColorSpaceCreateDeviceGray(), colorComponents: [1, 1, 1, 1, 0, 1], locations: [0, 0.22, 0.52], count: 3)!
    fade.drawLinearGradient(fg, start: .zero, end: CGPoint(x: W * scale, y: 0), options: [.drawsAfterEndLocation])
    ctx.saveGState()
    ctx.clip(to: CGRect(x: 0, y: 0, width: W, height: H), mask: fade.makeImage()!)
    let maze = ctx
    maze.setLineCap(.round)
    for (i, (r, openings)) in rings.enumerated() {
        let a = 0.16 - CGFloat(i) * 0.012
        maze.setStrokeColor(parchment(a)); maze.setLineWidth(i == 0 ? 1.4 : 1.1)
        maze.addPath(ringPath(app, r, gaps: openings.map { ($0.0, $0.1 / r * 180 / .pi) })); maze.strokePath()
    }
    // A few cross walls, never on the way out.
    for (j, a) in [(0, 30.0), (1, 160.0), (2, 300.0), (3, 210.0), (4, 350.0), (5, 60.0), (6, 135.0)] as [(Int, CGFloat)] {
        maze.setStrokeColor(parchment(0.12 - CGFloat(j) * 0.01)); maze.setLineWidth(1.1)
        maze.move(to: polar(app, rings[j].0, a)); maze.addLine(to: polar(app, rings[j + 1].0, a)); maze.strokePath()
    }
    // A bezel with a degree scale, as on the page's maze.
    let rb: CGFloat = 318
    maze.setStrokeColor(parchment(0.08)); maze.setLineWidth(1)
    maze.addEllipse(in: CGRect(x: app.x - rb, y: app.y - rb, width: 2 * rb, height: 2 * rb)); maze.strokePath()
    for a in stride(from: 0, to: 360, by: 5) {
        let major = a % 30 == 0
        maze.setStrokeColor(parchment(major ? 0.14 : 0.07))
        maze.move(to: polar(app, rb, CGFloat(a))); maze.addLine(to: polar(app, rb + (major ? 9 : 5), CGFloat(a))); maze.strokePath()
    }
    ctx.restoreGState()

    // The brass line from the app to Applications: leaves the centre ring through its door.
    let x0 = app.x + 84, x1 = dest.x - 84
    ctx.setStrokeColor(brass()); ctx.setLineWidth(1.5); ctx.setLineCap(.round); ctx.setLineJoin(.round)
    ctx.move(to: CGPoint(x: x0, y: app.y)); ctx.addLine(to: CGPoint(x: x1, y: app.y)); ctx.strokePath()
    ctx.move(to: CGPoint(x: x1 - 7, y: app.y + 6)); ctx.addLine(to: CGPoint(x: x1, y: app.y))
    ctx.addLine(to: CGPoint(x: x1 - 7, y: app.y - 6)); ctx.strokePath()
    ctx.setFillColor(brass()); ctx.fillEllipse(in: CGRect(x: x0 - 3, y: app.y - 3, width: 6, height: 6))

    // The caption, above the line: serif, small, in the page's muted parchment.
    let font = serifFont(14, italic: true)
    print("caption font: \(CTFontCopyPostScriptName(font)) (\(CTFontCopyFamilyName(font)))")
    let text = NSAttributedString(string: "Drag to Applications", attributes: [
        NSAttributedString.Key(kCTFontAttributeName as String): font,
        NSAttributedString.Key(kCTForegroundColorAttributeName as String): parchment(0.66),
        NSAttributedString.Key(kCTKernAttributeName as String): 0.2,
    ])
    let line = CTLineCreateWithAttributedString(text)
    let bounds = CTLineGetImageBounds(line, ctx)
    ctx.textPosition = CGPoint(x: (x0 + x1) / 2 - bounds.width / 2 - bounds.minX, y: app.y + 14)
    CTLineDraw(line, ctx)

    writePNG(ctx, url, dpi: 72 * k)
}

// MARK: - Main

let args = CommandLine.arguments
guard args.count == 2 else {
    FileHandle.standardError.write("usage: swift scripts/make_art.swift OUT_DIR\n".data(using: .utf8)!)
    exit(2)
}
let out = URL(fileURLWithPath: args[1], isDirectory: true)
let iconset = out.appendingPathComponent("AppIcon.iconset", isDirectory: true)
try? FileManager.default.removeItem(at: iconset)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)

for base in [16, 32, 128, 256, 512] {
    renderIcon(pixels: base, to: iconset.appendingPathComponent("icon_\(base)x\(base).png"))
    renderIcon(pixels: base * 2, to: iconset.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
renderBackground(scale: 1, to: out.appendingPathComponent("background.png"))
renderBackground(scale: 2, to: out.appendingPathComponent("background@2x.png"))
print(out.path)
