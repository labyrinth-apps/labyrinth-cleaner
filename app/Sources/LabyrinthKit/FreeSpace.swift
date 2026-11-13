import Foundation

// Pure logic for the menu-bar item, kept apart from AppKit so it can be unit-tested.
//
// Thresholds use decimal GB (1000^3) because the number shown is Finder's,
// and Finder counts in decimal: "amber below 30 GB" means below what it shows as 30 GB.

public let bytesPerGB: Int64 = 1_000_000_000
public let amberBelow: Int64 = 30 * bytesPerGB
public let redBelow: Int64 = 15 * bytesPerGB
public let rearmAbove: Int64 = 20 * bytesPerGB

public enum Level { case normal, amber, red }

public func level(freeBytes: Int64) -> Level {
    if freeBytes < redBelow { return .red }
    if freeBytes < amberBelow { return .amber }
    return .normal
}

public struct NotifyState {
    public var notified = false
    public init() {}
}

/// True exactly when a "disk almost full" notification should fire: the first
/// reading below 15 GB. It fires again only after free space went back above 20 GB.
public func step(_ s: inout NotifyState, freeBytes: Int64) -> Bool {
    if !s.notified && freeBytes < redBelow {
        s.notified = true
        return true
    }
    if s.notified && freeBytes > rearmAbove {
        s.notified = false
    }
    return false
}

/// Same style as Finder ("57.1 GB").
public func formatFree(_ bytes: Int64) -> String {
    let f = ByteCountFormatter()
    f.countStyle = .file
    return f.string(fromByteCount: bytes)
}
