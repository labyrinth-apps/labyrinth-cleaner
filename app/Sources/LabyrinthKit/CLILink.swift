import Foundation

public enum LinkState: Equatable { case missing, symlink(to: String), notALink }

public enum LinkPlan: Equatable {
    case create, alreadyInstalled
    case replaceOurs(old: String)       // old target is inside a "Labyrinth Cleaner.app": replaced silently
    case askReplace(old: String)        // any other symlink (e.g. a source checkout): ask first
    case refuse(String)                 // a real file or folder
}

public func planLink(state: LinkState, target: String) -> LinkPlan {
    switch state {
    case .missing: return .create
    case .notALink: return .refuse("~/.local/bin/devclean exists and isn't a link. Remove it first.")
    case .symlink(let old):
        if old == target { return .alreadyInstalled }
        if old.contains("/Labyrinth Cleaner.app/") { return .replaceOurs(old: old) }
        return .askReplace(old: old)
    }
}

/// lstat-based, so a dangling symlink still counts as a symlink.
public func linkState(at path: String) -> LinkState {
    var st = stat()
    guard lstat(path, &st) == 0 else { return .missing }
    if (st.st_mode & S_IFMT) == S_IFLNK,
       let dest = try? FileManager.default.destinationOfSymbolicLink(atPath: path) {
        return .symlink(to: dest)
    }
    return .notALink
}

/// Always `<home>/.local/bin`; `/usr/local/bin` only when it exists and is writable.
public func cliDestinations(home: String, isWritable: (String) -> Bool, exists: (String) -> Bool) -> [String] {
    var dirs = [home + "/.local/bin"]
    if exists("/usr/local/bin") && isWritable("/usr/local/bin") { dirs.append("/usr/local/bin") }
    return dirs
}
