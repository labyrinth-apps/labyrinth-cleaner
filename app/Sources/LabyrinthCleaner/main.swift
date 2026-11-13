import AppKit

// Labyrinth Cleaner: a menu-bar app. The window arrives in a later task; for now
// "Open Labyrinth Cleaner" starts the app-owned server and logs its port.

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
