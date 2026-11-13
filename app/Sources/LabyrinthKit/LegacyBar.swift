import Foundation

/// The older DevcleanBar menu-bar item has this bundle identifier.
public func legacyBarRunning(bundleIDs: [String]) -> Bool {
    bundleIDs.contains("local.devclean.bar")
}
