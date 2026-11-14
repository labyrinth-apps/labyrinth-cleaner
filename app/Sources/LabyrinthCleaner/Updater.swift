import AppKit
import Sparkle

/// Wraps Sparkle's standard updater. When disabled (no key in this build, or a fixture run) the updater never starts.
final class Updater {
    private let controller: SPUStandardUpdaterController
    private let enabled: Bool

    init(enabled: Bool) {
        self.enabled = enabled
        controller = SPUStandardUpdaterController(startingUpdater: enabled, updaterDelegate: nil, userDriverDelegate: nil)
    }

    var canCheck: Bool { enabled && controller.updater.canCheckForUpdates }

    func checkForUpdates() {
        guard canCheck else { return }
        controller.checkForUpdates(nil)
    }
}
