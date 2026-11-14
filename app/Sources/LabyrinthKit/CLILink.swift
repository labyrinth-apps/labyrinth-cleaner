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

/// Always `<home>/.local/bin`; `/usr/local/bin` only when it exists and is writable, and never in a
/// fixture run (`fixtureHome` set): that one stays inside the fixture home.
public func cliDestinations(home: String, fixtureHome: String? = nil,
                            isWritable: (String) -> Bool, exists: (String) -> Bool) -> [String] {
    if let fixtureHome { return [fixtureHome + "/.local/bin"] }
    var dirs = [home + "/.local/bin"]
    if exists("/usr/local/bin") && isWritable("/usr/local/bin") { dirs.append("/usr/local/bin") }
    return dirs
}

/// True when this copy of the app lives somewhere that disappears or can't be registered:
/// a mounted DMG (a read-only volume) or an App Translocation path. Anything that records the
/// app's path (the command line link, the login item) must ask the user to move it first.
public func mustMoveToApplications(bundlePath: String, volumeIsReadOnly: Bool) -> Bool {
    volumeIsReadOnly || bundlePath.contains("/AppTranslocation/")
}

/// "Move Labyrinth Cleaner to your Applications folder first, then <then>."
public func moveToApplicationsMessage(then: String) -> String {
    "Move Labyrinth Cleaner to your Applications folder first, then \(then)."
}

public let cliMoveToApplicationsMessage = moveToApplicationsMessage(then: "install the command line tool")
public let loginMoveToApplicationsMessage = moveToApplicationsMessage(then: "turn on Open at Login")

/// nil when the command line tool may link to this copy of the app; the message otherwise.
public func cliInstallBlocker(bundlePath: String, volumeIsReadOnly: Bool) -> String? {
    mustMoveToApplications(bundlePath: bundlePath, volumeIsReadOnly: volumeIsReadOnly) ? cliMoveToApplicationsMessage : nil
}

/// nil when Open at Login may register this copy of the app; the message otherwise.
public func loginItemBlocker(bundlePath: String, volumeIsReadOnly: Bool) -> String? {
    mustMoveToApplications(bundlePath: bundlePath, volumeIsReadOnly: volumeIsReadOnly) ? loginMoveToApplicationsMessage : nil
}

/// The open-at-login switch is off-limits in fixture runs.
public let fixtureLoginMessage = "Open at Login isn't available in fixture runs."
