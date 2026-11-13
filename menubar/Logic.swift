import Foundation

// Pure logic for the menu-bar item, kept apart from AppKit so it can be checked
// with `swiftc -parse-as-library Logic.swift logic_check.swift`.
//
// Thresholds use decimal GB (1000^3) because the number shown is Finder's,
// and Finder counts in decimal: "amber below 30 GB" means below what it shows as 30 GB.

let bytesPerGB: Int64 = 1_000_000_000
let amberBelow: Int64 = 30 * bytesPerGB
let redBelow: Int64 = 15 * bytesPerGB
let rearmAbove: Int64 = 20 * bytesPerGB

enum Level { case normal, amber, red }

func level(freeBytes: Int64) -> Level {
    if freeBytes < redBelow { return .red }
    if freeBytes < amberBelow { return .amber }
    return .normal
}

struct NotifyState { var notified = false }

/// True exactly when a "disk almost full" notification should fire: the first
/// reading below 15 GB. It fires again only after free space went back above 20 GB.
func step(_ s: inout NotifyState, freeBytes: Int64) -> Bool {
    if !s.notified && freeBytes < redBelow {
        s.notified = true
        return true
    }
    if s.notified && freeBytes > rearmAbove {
        s.notified = false
    }
    return false
}

/// Alert text when `devclean ui` (started by Clean up…) exits with an error, or nil
/// when it ended normally or was stopped by a signal (Ctrl-C, logout).
func launchFailureMessage(status: Int32, signaled: Bool, stderr: String) -> String? {
    if signaled || status == 0 { return nil }
    let tail = stderr.split(separator: "\n", omittingEmptySubsequences: true).suffix(5)
        .joined(separator: "\n")
    return "devclean ui stopped with exit \(status)." + (tail.isEmpty ? "" : "\n\n" + tail)
}

/// Same style as Finder ("57.1 GB").
func formatFree(_ bytes: Int64) -> String {
    let f = ByteCountFormatter()
    f.countStyle = .file
    return f.string(fromByteCount: bytes)
}
