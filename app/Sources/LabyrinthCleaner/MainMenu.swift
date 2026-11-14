import AppKit

/// The app's main menu (shown while the window is open): About, Hide, Quit; Copy, Select All; Close, Minimize.
func installMainMenu(updater: Updater) {
    let name = "Labyrinth Cleaner"
    let main = NSMenu()

    func add(_ menu: NSMenu, _ title: String, _ action: Selector?, _ key: String = "", _ mods: NSEvent.ModifierFlags = .command) {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.keyEquivalentModifierMask = mods
        menu.addItem(item)
    }
    func submenu(_ title: String) -> NSMenu {
        let host = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let m = NSMenu(title: title)
        host.submenu = m
        main.addItem(host)
        return m
    }

    let app = submenu(name)
    add(app, "About \(name)", #selector(NSApplication.orderFrontStandardAboutPanel(_:)))
    let updates = NSMenuItem(title: "Check for Updates…", action: #selector(MenuUpdateTarget.check), keyEquivalent: "")
    updates.target = MenuUpdateTarget.shared
    MenuUpdateTarget.shared.updater = updater
    app.addItem(updates)
    app.addItem(.separator())
    add(app, "Hide \(name)", #selector(NSApplication.hide(_:)), "h")
    add(app, "Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option])
    app.addItem(.separator())
    add(app, "Quit \(name)", #selector(NSApplication.terminate(_:)), "q")

    let edit = submenu("Edit")
    add(edit, "Copy", #selector(NSText.copy(_:)), "c")
    add(edit, "Select All", #selector(NSText.selectAll(_:)), "a")

    let window = submenu("Window")
    add(window, "Close", #selector(NSWindow.performClose(_:)), "w")
    add(window, "Minimize", #selector(NSWindow.performMiniaturize(_:)), "m")

    NSApp.mainMenu = main
    NSApp.windowsMenu = window
}

/// Target for the app-menu "Check for Updates…" item; validates against the updater so it is disabled when updates are off.
final class MenuUpdateTarget: NSObject, NSMenuItemValidation {
    static let shared = MenuUpdateTarget()
    var updater: Updater?
    @objc func check() { updater?.checkForUpdates() }
    func validateMenuItem(_ item: NSMenuItem) -> Bool { updater?.canCheck ?? false }
}
