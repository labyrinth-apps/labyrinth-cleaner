import Foundation

/// Whether the Sparkle updater may start: only with a real signing key in the bundle and never in fixture runs
/// (fixture runs must not touch the network).
public func updaterEnabled(publicKey: String?, fixture: Bool) -> Bool {
    guard !fixture else { return false }
    guard let key = publicKey?.trimmingCharacters(in: .whitespacesAndNewlines), !key.isEmpty else { return false }
    return true
}
