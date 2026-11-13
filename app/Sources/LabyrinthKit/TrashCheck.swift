import Foundation
import Darwin

public enum TrashTarget: Equatable { case downloadsFile, archive }
public struct TrashRefusal: Error, Equatable { public let message: String }

/// `realpath(3)` as a String; nil if the path does not resolve.
public func realpathString(_ path: String) -> String? {
    guard let r = realpath(path, nil) else { return nil }
    defer { free(r) }
    return String(cString: r)
}

private func lstatMode(_ path: String) -> mode_t? {
    var st = stat()
    guard lstat(path, &st) == 0 else { return nil }
    return st.st_mode & S_IFMT
}

private func cleanComponents(_ path: String) -> [String]? {
    guard path.hasPrefix("/") else { return nil }
    let parts = path.split(separator: "/", omittingEmptySubsequences: false).dropFirst().map(String.init)
    if parts.isEmpty || parts.contains(where: { $0.isEmpty || $0 == "." || $0 == ".." }) { return nil }
    return parts
}

/// Mirrors devclean_core's REVIEW rules. lstat (no-follow) on every component below `home`.
public func checkTrashable(path: String, home: String) -> Result<TrashTarget, TrashRefusal> {
    func refuse(_ why: String) -> Result<TrashTarget, TrashRefusal> {
        .failure(TrashRefusal(message: why))
    }
    guard let homeParts = cleanComponents(home) else { return refuse("not a clean absolute home: \(home)") }
    guard let pathParts = cleanComponents(path) else { return refuse("not a clean absolute path: \(path)") }
    guard pathParts.count > homeParts.count, Array(pathParts.prefix(homeParts.count)) == homeParts else {
        return refuse("not inside the home folder: \(path)")
    }
    let rel = Array(pathParts.dropFirst(homeParts.count))

    let target: TrashTarget
    let wantMode: mode_t
    if rel[0] == "Downloads" && (rel.count == 2 || rel.count == 3) {
        target = .downloadsFile
        wantMode = S_IFREG
    } else if rel.count == 6, Array(rel.prefix(4)) == ["Library", "Developer", "Xcode", "Archives"],
              rel[5].hasSuffix(".xcarchive") {
        target = .archive
        wantMode = S_IFDIR
    } else {
        return refuse("not a Downloads file or an Xcode archive: \(path)")
    }

    var cur = home
    for (i, comp) in rel.enumerated() {
        cur += "/" + comp
        guard let mode = lstatMode(cur) else { return refuse("\(cur) is gone") }
        if mode == S_IFLNK { return refuse("is a symlink: \(cur)") }
        if i < rel.count - 1 {
            if mode != S_IFDIR { return refuse("not a folder: \(cur)") }
        } else if mode != wantMode {
            return refuse(target == .downloadsFile ? "not a regular file: \(cur)" : "not an .xcarchive folder: \(cur)")
        }
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
    if case .failure(let refusal) = checkTrashable(path: path, home: home) {
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
    switch lstatMode(trashDir) {
    case nil:
        if mkdir(trashDir, 0o700) != 0 && errno != EEXIST {
            return (4, "cannot create \(trashDir): \(String(cString: strerror(errno)))")
        }
        fallthrough
    case S_IFDIR?:
        // Re-check after creation: a racing symlink must not be followed.
        if lstatMode(trashDir) != S_IFDIR { return (3, "REFUSED: \(trashDir) is not a plain folder") }
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
