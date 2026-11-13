import AppKit

// Labyrinth Cleaner: a menu-bar app that shows its window on launch (not on login launch).

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
