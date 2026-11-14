import Foundation

/// Where this running copy of the app lives, for the LabyrinthKit move-first blockers.
enum AppLocation {
    static var bundlePath: String { Bundle.main.bundlePath }
    static var volumeIsReadOnly: Bool {
        (try? Bundle.main.bundleURL.resourceValues(forKeys: [.volumeIsReadOnlyKey]))?.volumeIsReadOnly ?? false
    }
}
