import Foundation
import Darwin

public enum TrashTarget: Equatable { case downloadsFile, archive, application }
public struct TrashRefusal: Error, Equatable { public let message: String }

/// `realpath(3)` as a String; nil if the path does not resolve.
public func realpathString(_ path: String) -> String? {
    guard let r = realpath(path, nil) else { return nil }
    defer { free(r) }
    return String(cString: r)
}

private let slash = UInt8(ascii: "/")

private func cstr(_ bytes: [UInt8]) -> [CChar] { bytes.map { CChar(bitPattern: $0) } + [0] }

private func lstatMode(_ bytes: [UInt8]) -> mode_t? {
    var st = stat()
    guard lstat(cstr(bytes), &st) == 0 else { return nil }
    return st.st_mode & S_IFMT
}

private func lstatOwner(_ bytes: [UInt8]) -> uid_t? {
    var st = stat()
    guard lstat(cstr(bytes), &st) == 0 else { return nil }
    return st.st_uid
}

private func realpathBytes(_ bytes: [UInt8]) -> [UInt8]? {
    guard let r = realpath(cstr(bytes), nil) else { return nil }
    defer { free(r) }
    return Array(UnsafeBufferPointer(start: UnsafeRawPointer(r).assumingMemoryBound(to: UInt8.self),
                                     count: strlen(r)))
}

/// Components of an absolute path, split on raw bytes (never on grapheme clusters).
/// nil if not absolute, or any component is empty, "." or ".." or the path holds a NUL.
private func cleanComponents(_ bytes: [UInt8]) -> [[UInt8]]? {
    guard bytes.first == slash, !bytes.contains(0) else { return nil }
    let parts = bytes.dropFirst().split(separator: slash, omittingEmptySubsequences: false).map { Array($0) }
    let dot: [UInt8] = [0x2E], dotdot: [UInt8] = [0x2E, 0x2E]
    if parts.contains(where: { $0.isEmpty || $0 == dot || $0 == dotdot }) { return nil }
    return parts
}

private func b(_ s: String) -> [UInt8] { Array(s.utf8) }

/// Mirrors devclean_core's REVIEW rules. lstat (no-follow) on every component below `home`
/// (below `/` for the system Applications folder, which only the real home may use).
/// Every comparison and every syscall uses the raw bytes of `path`.
public func checkTrashable(path: String, home: String, isRealHome: Bool,
                           uid: uid_t = getuid()) -> Result<TrashTarget, TrashRefusal> {
    func refuse(_ why: String) -> Result<TrashTarget, TrashRefusal> {
        .failure(TrashRefusal(message: why))
    }
    let pathBytes = b(path)
    guard let homeParts = cleanComponents(b(home)) else { return refuse("not a clean absolute home: \(home)") }
    guard let pathParts = cleanComponents(pathBytes) else { return refuse("not a clean absolute path: \(path)") }
    let systemApps = [b("Applications")]
    let inSystemApps = isRealHome && pathParts.count > 1 && Array(pathParts.prefix(1)) == systemApps
    let inHome = pathParts.count > homeParts.count && Array(pathParts.prefix(homeParts.count)) == homeParts
    guard inSystemApps || inHome else { return refuse("not inside the home folder: \(path)") }
    // Components that are lstat-ed, and the bytes of the folder they hang off.
    let baseParts = inSystemApps ? [] : homeParts
    let rel = Array(pathParts.dropFirst(baseParts.count))

    // Application roots, relative to `/`: every root is checked against the full path.
    var appRoots: [[[UInt8]]] = [homeParts + [b("Applications")]]
    appRoots.append(isRealHome ? systemApps : homeParts + [b(".fixture-root"), b("Applications")])

    let target: TrashTarget
    let wantMode: mode_t
    if let root = appRoots.first(where: { pathParts.count > $0.count && Array(pathParts.prefix($0.count)) == $0 }) {
        let depth = pathParts.count - root.count
        guard depth == 1 || depth == 2, pathParts.last!.count > 4, pathParts.last!.suffix(4) == b(".app")[...] else {
            return refuse("not an application bundle: \(path)")
        }
        // Mirrors the core: never a bundle inside another bundle (Foo.app/Bar.app).
        if depth == 2, pathParts[pathParts.count - 2].suffix(4) == b(".app")[...] {
            return refuse("inside another application: \(path)")
        }
        target = .application
        wantMode = S_IFDIR
    } else if rel[0] == b("Downloads") && (rel.count == 2 || rel.count == 3) {
        target = .downloadsFile
        wantMode = S_IFREG
    } else if rel.count == 6, Array(rel.prefix(4)) == ["Library", "Developer", "Xcode", "Archives"].map(b),
              rel[5].count > 0, rel[5].suffix(10) == b(".xcarchive")[...] {
        target = .archive
        wantMode = S_IFDIR
    } else {
        return refuse("not a Downloads file, an Xcode archive or an application bundle: \(path)")
    }

    // Walk the path's own bytes: the string checked is the string acted on.
    var cur = Array(pathBytes.prefix(baseParts.reduce(0) { $0 + $1.count + 1 }))
    for (i, comp) in rel.enumerated() {
        cur += [slash] + comp
        let shown = String(decoding: cur, as: UTF8.self)
        guard let mode = lstatMode(cur) else { return refuse("\(shown) is gone") }
        if mode == S_IFLNK { return refuse("is a symlink: \(shown)") }
        if i < rel.count - 1 {
            if mode != S_IFDIR { return refuse("not a folder: \(shown)") }
        } else if mode != wantMode {
            switch target {
            case .downloadsFile: return refuse("not a regular file: \(shown)")
            case .archive: return refuse("not an .xcarchive folder: \(shown)")
            case .application: return refuse("not an application bundle: \(shown)")
            }
        }
    }
    if target == .application, lstatOwner(pathBytes) != uid { return refuse("not owned by you: \(path)") }
    // Mirrors the core's `realpath(path) != path` refusal.
    guard let real = realpathBytes(pathBytes), real == pathBytes else {
        return refuse("resolves elsewhere: \(path)")
    }
    return .success(target)
}

/// Exit codes: 0 moved, 2 usage, 3 refused (message starts "REFUSED: "), 4 move failed.
/// `arguments` excludes the program name: `--home H PATH`.
public func runTrashHelper(arguments: [String], realHome: String,
                           trash: (URL) throws -> Void) -> (code: Int32, message: String) {
    guard arguments.count == 3, arguments[0] == "--home", arguments[2].hasPrefix("/") else {
        return (2, "usage: labyrinth-trash --home HOME ABSOLUTE_PATH")
    }
    guard let home = realpathString(arguments[1]) else {
        return (2, "usage: --home does not resolve: \(arguments[1])")
    }
    let path = arguments[2]
    if case .failure(let refusal) = checkTrashable(path: path, home: home, isRealHome: home == realHome) {
        return (3, "REFUSED: \(refusal.message)")
    }
    if home == realHome {
        do {
            try trash(URL(fileURLWithPath: path))
            return (0, "")
        } catch {
            return (4, "move to Trash failed: \(error.localizedDescription)")
        }
    }
    return moveIntoFixtureTrash(path: path, home: home)
}

private func moveIntoFixtureTrash(path: String, home: String) -> (code: Int32, message: String) {
    let trashDir = home + "/.Trash"
    switch lstatMode(b(trashDir)) {
    case nil:
        if mkdir(trashDir, 0o700) != 0 && errno != EEXIST {
            return (4, "cannot create \(trashDir): \(String(cString: strerror(errno)))")
        }
        fallthrough
    case S_IFDIR?:
        // Re-check after creation: a racing symlink must not be followed.
        if lstatMode(b(trashDir)) != S_IFDIR { return (3, "REFUSED: \(trashDir) is not a plain folder") }
    default:
        return (3, "REFUSED: \(trashDir) is a symlink or not a folder")
    }
    let name = (path as NSString).lastPathComponent as NSString
    let ext = name.pathExtension
    let stem = name.deletingPathExtension
    var n = 1
    while true {
        let candidate = n == 1 ? String(name) : (ext.isEmpty ? "\(stem) \(n)" : "\(stem) \(n).\(ext)")
        let dest = trashDir + "/" + candidate
        // RENAME_EXCL: atomic never-overwrite.
        if renamex_np(path, dest, UInt32(RENAME_EXCL)) == 0 { return (0, "") }
        if errno == EEXIST { n += 1; continue }
        return (4, "move failed: \(String(cString: strerror(errno)))")
    }
}
